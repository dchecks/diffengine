#!/usr/bin/env python
# -*- coding: utf-8 -*-

# maybe this module should be broken up into multiple files, or maybe not ...

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/63.0.3239.84 Safari/537.36"
# """diffengine/0.1.2 (+https://github.com/docnow/diffengine)"

import os
import re
import sys
import time
import yaml
import bleach
import codecs
import jinja2
import tweepy
import logging
import htmldiff
import requests
import feedparser
import subprocess
import readability
import unicodedata
import argparse

from peewee import *
from datetime import datetime
from selenium import webdriver
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
from argparse import RawTextHelpFormatter

home = None
config = {}
db = SqliteDatabase(None)

trace_output = False


class BaseModel(Model):
    class Meta:
        database = db


class Feed(BaseModel):
    url = CharField(primary_key=True)
    name = CharField()
    created = DateTimeField(default=datetime.utcnow)

    @staticmethod
    def sanitize_url(url):
        return url.replace('http://', 'https://')

    @property
    def entries(self):
        return (Entry.select()
                .join(FeedEntry)
                .join(Feed)
                .where(Feed.url==self.url)
                .order_by(Entry.created.desc()))

    def refresh_feed(self):
        """
        Gets the feed and creates new entries for new content. The number
        of new entries created will be returned.
        """
        logging.info("fetching feed: %s", self.url)
        try:
            resp = _get(self.url)
            feed = feedparser.parse(resp.text)
        except Exception as e:
            logging.error("unable to fetch feed %s: %s", self.url, e)
            return 0
        count = 0
        dupe_count = 0
        dupe_table = []
        for feed_entry in feed.entries:
            # note: look up with url only, because there may be 
            # overlap bewteen feeds, especially when a large newspaper
            # has multiple feeds
            s_url = Feed.sanitize_url(feed_entry.link)
            entry, created = Entry.get_or_create(url=s_url)

            if entry in dupe_table:
                dupe_count += 1
            else:
                dupe_table.append(entry)

                if created:
                    FeedEntry.create(entry=entry, feed=self)
                    logging.info("found new entry: %s", feed_entry.link)
                    count += 1
                elif len(entry.feeds.where(Feed.url == self.url)) == 0:
                    FeedEntry.create(entry=entry, feed=self)
                    logging.debug("found entry from another feed: %s", feed_entry.link)
                    count += 1

        if dupe_count > 0:
            logging.info('Found duplicates in the feed, %s', str(dupe_table))
        return count


class Entry(BaseModel):
    url = CharField()
    created = DateTimeField(default=datetime.utcnow)
    checked = DateTimeField(default=datetime.utcnow)

    @property
    def feeds(self):
        return (Feed.select()
                .join(FeedEntry)
                .join(Entry)
                .where(Entry.id==self.id))

    @property   
    def stale(self):
        """
        A heuristic for checking new content very often, and checking 
        older content less frequently. If an entry is deemed stale then
        it is worth checking again to see if the content has changed.
        """

        # never been checked before it's obviously stale
        if not self.checked:
            return True

        # time since the entry was created
        hotness = (datetime.utcnow() - self.created).seconds
        if hotness == 0:
            return True

        # don't bother checking if it's older than 1 month
        if hotness - 2628000 > 0:
            return False

        # time since the entry was last checked
        staleness = (datetime.utcnow() - self.checked).seconds

        # ratio of staleness to hotness
        r = staleness / float(hotness)

        # TODO: allow this magic number to be configured per feed?
        if r >= 0.2:
            logging.debug("%s is stale (r=%f)", self.url, r)
            return True

        logging.debug("%s not stale (r=%f)", self.url, r)
        return False

    def get_latest(self, archive_enabled):
        """
        get_latest is the heart of the application. It will get the current 
        version on the web, extract its summary with readability and compare 
        it against a previous version. If a difference is found it will 
        compute the diff, save it as html and png files, and tell Internet 
        Archive to create a snapshot.

        If a new version was found it will be returned, otherwise None will
        be returned.
        """

        # make sure we don't go too fast
        time.sleep(1)

        # fetch the current readability-ized content for the page
        logging.info("checking %s", self.url)
        try:
            resp = _get(self.url)
        except Exception as e:
            logging.error("unable to fetch %s: %s", self.url, e)
            return None

        if resp.status_code != 200:
            logging.warning("Got %s when fetching %s", resp.status_code, self.url)
            return None

        if trace_output:
            logging.debug("-- Trace response text from %s\n\n%s", self.url, resp.text)
        doc = readability.Document(resp.text)
        title = doc.title()
        summary = doc.summary(html_partial=True)
        summary = bleach.clean(summary, tags=["p"], strip=True)
        summary = _normal(summary)
        logging.debug("Response processed for entry %s", self.id)
        # in case there was a redirect, and remove utm style marketing
        canonical_url = _remove_utm(resp.url)

        if canonical_url != self.url:
            logging.debug("URL changed\n - From: %s\n - To: %s\n - Response Status: %s",
                          self.url, canonical_url, resp.status_code)

        # get the latest version, if we have one
        versions = EntryVersion.select().where(EntryVersion.url==canonical_url)
        versions = versions.order_by(-EntryVersion.created)
        if len(versions) == 0:
            old = None
        else:
            old = versions[0]

        # compare what we got against the latest version and create a 
        # new version if it looks different, or is brand new (no old version)
        new = None

        # use _equal to determine if the summaries are the same
        if not old or old.title != title or not _equal(old.summary, summary):
            new = EntryVersion.create(
                title=title,
                url=canonical_url,
                summary=summary,
                entry=self
            )
            if archive_enabled:
                new.archive()
            if old:
                logging.debug("found new version %s", old.entry.url)
                diff = Diff.create(old=old, new=new)
                if not diff.generate():
                    logging.warning("html diff showed no changes: %s", self.url)
                    new.delete()
                    new = None
            else:
                logging.debug("found first version: %s", self.url)
        else:
            logging.debug("content hasn't changed %s", self.url)

        self.checked = datetime.utcnow()
        self.save()

        return new


class FeedEntry(BaseModel):
    feed = ForeignKeyField(Feed)
    entry = ForeignKeyField(Entry)
    created = DateTimeField(default=datetime.utcnow)


class EntryVersion(BaseModel):
    title = CharField()
    url = CharField()
    summary = CharField()
    created = DateTimeField(default=datetime.utcnow)
    archive_url = CharField(null=True)
    entry = ForeignKeyField(Entry, related_name='versions')

    @property
    def diff(self):
        """
        The diff that this version created. It can be None if
        this is the first version of a given entry.
        """
        try:
            return Diff.select().where(Diff.new_id==self.id).get()
        except:
            return None

    @property
    def next_diff(self):
        """
        The diff that this version participates in as the previous
        version. I know that's kind of a tongue twister. This can be
        None if this version is the latest we know about.
        """
        try:
            return Diff.select().where(Diff.old_id==self.id).get()
        except:
            return None

    @property
    def html(self):
        return "<h1>%s</h1>\n\n%s" % (self.title, self.summary)

    def archive(self):
        save_url = "https://web.archive.org/save/" + self.url
        try:
            resp = _get(save_url)
            wayback_id = resp.headers.get("Content-Location")
            if wayback_id:
                self.archive_url = "https://wayback.archive.org" + wayback_id
                logging.debug("archived version at %s", self.archive_url)
                self.save()
                return self.archive_url
            else:
                logging.error("unable to get archive id from %s: %s",
                        self.archive_url, resp.headers)

        except Exception as e:
            logging.error("unexpected archive.org response for %s: %s", save_url, e)
        return None

class Diff(BaseModel):
    old = ForeignKeyField(EntryVersion, related_name="prev_diffs")
    new = ForeignKeyField(EntryVersion, related_name="next_diffs")
    created = DateTimeField(default=datetime.utcnow)
    tweeted = DateTimeField(null=True)
    blogged = DateTimeField(null=True)

    @property
    def html_path(self):
        # use prime number to spread across directories
        created_day = self.created.strftime('%Y-%m-%d')
        path = home_path("diffs/%s/%s.html" % (created_day, self.id))
        if not os.path.isdir(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        return path

    def screenshot_path(self, path=html_path):
        return path.replace(".html", ".jpg")

    def thumbnail_path(self, path=html_path):
        return path.replace('.jpg', '-thumb.jpg')

    def generate(self, path=html_path):
        html = self.generate_diff_html(path)
        if html:
            codecs.open(path, "w", 'utf8').write(html)
            self.generate_diff_images(path)
            return True
        else:
            logging.error("Failed to generate diff for %s", path)
            return False

    ins_diff_exclusions = ["<ins>\* Comments",
                       "<ins>Comments",]

    del_diff_exclusions = ["<del>\* Comments",
                          "<del>Comments",
                          "Last updated <del>",]

    def validate_diff(self, diff):
        if '<ins>' not in diff and '<del>' not in diff:
            logging.debug('No change found')
            return False

        logging.info("Diff found, checking exclusions")
        ins_count = len(re.findall("<ins>", diff))
        del_count = len(re.findall("<del>", diff))
        ins_exclusion_count = 0
        del_exclusion_count = 0

        for exclusion in self.ins_diff_exclusions:
            result = len(re.findall(exclusion, diff))
            ins_exclusion_count += result
            if result > 0:
                logging.debug('Matched insert exclusion: %s', exclusion)
        for exclusion in self.del_diff_exclusions:
            result = len(re.findall(exclusion, diff))
            del_exclusion_count += result
            if result > 0:
                logging.debug('Matched delete exclusion: %s', exclusion)

        if ins_count == ins_exclusion_count and del_count == del_exclusion_count:
            logging.info('Ignoring diff due to exclusion count, (ins: %s, del: %s)', ins_exclusion_count, del_exclusion_count)
            return False

        return True

    def generate_diff_html(self, path):
        if os.path.isfile(path):
            logging.error("Diff file already exists: %s",path)
            return None

        tmpl_path = os.path.join(os.path.dirname(__file__), "diff_template.html")
        if not os.path.isfile(tmpl_path):
            logging.error("Failed to find diff template: %s", tmpl_path)
            return None

        logging.debug("creating html diff: %s", path)
        diff = htmldiff.render_html_diff(self.old.html, self.new.html)
        if not self.validate_diff(diff):
            return None

        tmpl = jinja2.Template(codecs.open(tmpl_path, "r", "utf8").read())
        html = tmpl.render(
            title=self.new.title,
            url=self.old.entry.url,
            old_time=self.old.created,
            new_time=self.new.created,
            diff=diff
        )
        return html

    def generate_diff_images(self, html_path):
        if os.path.isfile(html_path):
            logging.error("Screenshot already exists at path: %s", html_path)
            return
        if not hasattr(self, 'browser'):
            phantomjs = config.get('phantomjs', 'phantomjs')
            self.browser = webdriver.PhantomJS(phantomjs)

        screenshot = self.screenshot_path(html_path)
        logging.debug("creating image screenshot %s", screenshot)
        self.browser.set_window_size(1400, 1000)
        self.browser.get(html_path)
        time.sleep(5) # give the page time to load
        self.browser.save_screenshot(screenshot)

        thumbnail = self.thumbnail_path(html_path)
        logging.debug("creating image thumbnail %s", thumbnail)
        self.browser.set_window_size(800, 400)
        self.browser.execute_script("clip()")
        self.browser.save_screenshot(thumbnail)


def setup_logging():
    path = '/var/log/diffengine/'
    if not os.path.exists(path):
        os.makedirs(path)
        logging.info("Created output directory %s", path)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        filename=path + 'diffengine.log',
        filemode="a"
    )
    logging.getLogger("readability.readability").setLevel(logging.WARNING)
    logging.getLogger("tweepy.binder").setLevel(logging.ERROR)
    logging.getLogger("peewee").setLevel(logging.ERROR)
    logging.getLogger("requests_oauthlib").setLevel(logging.ERROR)


def load_config(prompt=True):
    global config
    config_file = os.path.join(home, "config.yaml")
    if os.path.isfile(config_file):
        config = yaml.load(open(config_file))
    else:
        if not os.path.isdir(home):
            os.makedirs(home)
        if prompt:
            config = get_initial_config()
        yaml.dump(config, open(config_file, "w"), default_flow_style=False)


def get_initial_config():
    config = {"feeds": [], "phantomjs": "phantomjs"}

    while len(config['feeds']) == 0:
        url = input("What RSS/Atom feed would you like to monitor? ")
        feed = feedparser.parse(url)
        if len(feed.entries) == 0:
            print("Oops, that doesn't look like an RSS or Atom feed.")
        else:
            config['feeds'].append({
                "url": url,
                "name": feed.feed.title
            })

    answer = input("Would you like to set up tweeting edits? [Y/n] ")
    if answer.lower() == "y":
        print("Go to https://apps.twitter.com and create an application.")
        consumer_key = input("What is the consumer key? ")
        consumer_secret = input("What is the consumer secret? ")
        auth = tweepy.OAuthHandler(consumer_key, consumer_secret)
        auth.secure = True
        auth_url = auth.get_authorization_url()
        input("Log in to https://twitter.com as the user you want to tweet as and hit enter.")
        input("Visit %s in your browser and hit enter." % auth_url)
        pin = input("What is your PIN: ")
        token = auth.get_access_token(verifier=pin)
        config["twitter"] = {
            "consumer_key": consumer_key,
            "consumer_secret": consumer_secret
        }
        config["feeds"][0]["twitter"] = {
            "access_token": token[0],
            "access_token_secret": token[1]
        }

    print("Saved your configuration in %s/config.yaml" % home.rstrip("/"))
    print("Fetching initial set of entries.")

    return config


def home_path(rel_path):
    return os.path.join(home, rel_path)


def setup_db():
    global db
    db_file = config.get('db', home_path('diffengine.db'))
    logging.debug("connecting to db %s", db_file)
    db.init(db_file)
    db.connect()
    db.create_tables([Feed, Entry, FeedEntry, EntryVersion, Diff], safe=True)


def setup_phantomjs():
    phantomjs = config.get("phantomjs", "phantomjs")
    try:
        subprocess.check_output([phantomjs, '--version'])
    except FileNotFoundError:
        print("Please install phantomjs <http://phantomjs.org/>")
        print("If phantomjs is intalled but not in your path you can set the full path to phantomjs in your config: %s" % home.rstrip("/"))
        sys.exit()


def tweet_diff(diff, token):
    if 'twitter' not in config:
        logging.debug("twitter not configured")
        return
    elif not token:
        logging.debug("access token/secret not set up for feed")
        return
    elif diff.tweeted:
        logging.warn("diff %s has already been tweeted", diff.id)
        return

    logging.info("tweeting about  %s", diff.new.title)
    t = config['twitter']
    auth = tweepy.OAuthHandler(t['consumer_key'], t['consumer_secret'])
    auth.secure = True
    auth.set_access_token(token['access_token'], token['access_token_secret'])
    twitter = tweepy.API(auth)

    status = diff.new.title
    status = status.replace('| Stuff.co.nz', '')

    if len(status) >= 225:
        status = status[0:225] + "…"

    status += ' ' + diff.new.url

    try:
        twitter.update_with_media(diff.thumbnail_path, status)
        diff.tweeted = datetime.utcnow()
        logging.info("tweeted %s", status)
        diff.save()
    except Exception as e:
        logging.error("unable to tweet: %s", e)


def init(new_home, prompt=True):
    global home
    home = new_home
    load_config(prompt)
    setup_phantomjs()
    setup_logging()
    setup_db()


def rerun(entry_id):
    entry_version = EntryVersion.select() \
                    .join(Entry) \
                    .where(Entry.id == entry_id)\
                    .order_by(-EntryVersion.created)[0]
    diff = entry_version.diff()
    original_path = diff.html_path
    i = 1
    #Find the first available path
    while i < 100:
        rerun_path = original_path.replace(".html", "-rerun-" + str(i) + ".html")
        if not rerun_path.isfile():
            break
        else:
            i += 1

    diff.generate(rerun_path)
    logging.info("Rerun complete, check %s for output", rerun_path)


def process_feed():
    checked = skipped = new = tweeted = diffs = 0

    for feed_config in config.get('feeds', []):
        feed_name = feed_config['name']
        logging.debug("Processing feed: %s", feed_name)

        #Tweeting config - default to on but require config
        tweeting = True
        if feed_config.get('tweet', True) is False:
            logging.info("Tweeting disabled for feed %s", feed_name)
            tweeting = False
        if 'twitter' not in feed_config:
            logging.info("No twitter config for feed %s", feed_name)
            tweeting = False
        #Wayback config
        archive_enabled = feed_config.get('archive')
        if archive_enabled:
            logging.info("Wayback archive enabled for feed %s", feed_name)

        #Process feed
        feed, created = Feed.create_or_get(url=feed_config['url'], name=feed_config['name'])
        if created:
            logging.debug("Created new feed for %s", feed_config['url'])

        # get latest feed entries
        feed.refresh_feed()

        # get latest content for each entry
        for entry in feed.entries:
            if not entry.stale:
                skipped += 1
                logging.debug("%s - Skipping entry not stale", entry.id)
                continue
            checked += 1
            try:
                version = entry.get_latest(archive_enabled)
                if version:
                    new += 1
                if version and version.diff:
                    diffs += 1
                    if tweeting:
                        tweet_diff(version.diff, feed_config['twitter'])
                        tweeted += 1
            except Exception as e:
                logging.error('Unable to get latest for entry %s', entry.id)
                logging.error("Exception: ", e)

        logging.debug("Completed processing feed: %s", feed_config['name'])
    logging.info("Feed processing complete, new: %s, checked: %s, skipped: %s, diffs: %s, tweeted: %s",
                 new, checked, skipped, diffs, tweeted)


def main(args):
    home = args.home

    init(home)
    start_time = datetime.utcnow()
    logging.info("starting up with home=%s", home)

    if args.rerun:
        logging.info("Rerunning last diff for: %s", args.rerun)
        rerun(args.rerun)
    else:
        process_feed()

    elapsed = datetime.utcnow() - start_time
    logging.info("shutting down, elapsed=%s", elapsed)


def _dt(d):
    return d.strftime("%Y-%m-%d %H:%M:%S")


def _normal(s):
    # additional normalizations for readability + bleached text
    s = s.replace("\xa0", " ")
    s = s.replace('“', '"')
    s = s.replace('”', '"')
    s = s.replace("’", "'")
    s = s.replace("\n", " ")
    s = s.replace("­", "") 
    s = re.sub(r' +', ' ', s)
    s = s.strip()
    return s


def _equal(s1, s2):
    return _fingerprint(s1) == _fingerprint(s2)


punctuation = dict.fromkeys(i for i in range(sys.maxunicode) 
        if unicodedata.category(chr(i)).startswith('P'))


def _fingerprint(s):
    # make sure the string has been normalized, bleach everything, remove all 
    # whitespace and punctuation to create a psuedo fingerprint for the text 
    # for use during compararison
    s = _normal(s)
    s = bleach.clean(s, tags=[], strip=True)
    s = re.sub(r'\s+', '', s, flags=re.MULTILINE)
    s = s.translate(punctuation)
    return s


def _remove_utm(url):
    u = urlparse(url)
    q = parse_qs(u.query, keep_blank_values=True)
    new_q = dict((k, v) for k, v in q.items() if not k.startswith('utm_'))
    return urlunparse([
        u.scheme,
        u.netloc,
        u.path,
        u.params,
        urlencode(new_q, doseq=True),
        u.fragment
    ])


def _get(url):
    return requests.get(url, timeout=60, headers={"User-Agent": UA})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='', formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--home", help='Working directory', default=os.getcwd())
    parser.add_argument('--rerun', help='Regenerates the most recent diff of the given entity')

    args = parser.parse_args()
    main(args)
