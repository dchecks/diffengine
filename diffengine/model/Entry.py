# -*- coding: utf-8 -*-
import requests
from datetime import datetime
import re
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode


import bleach
import logging
import readability
import datetime as time
import unicodedata

import sys
from peewee import CharField, DateTimeField, BaseModel

#TODO Move to config
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/63.0.3239.84 Safari/537.36"


class Entry(BaseModel):
    url = CharField()
    created = DateTimeField(default=datetime.utcnow())
    checked = DateTimeField(default=datetime.utcnow())

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

        # time since the entry was last checked
        staleness = (datetime.utcnow() - self.checked).seconds

        # ratio of staleness to hotness
        r = staleness / float(hotness)

        # TODO: allow this magic number to be configured per  ?
        if r >= 0.2:
            logging.debug("%s is stale (r=%f)", self.url, r)
            return True

        logging.debug("%s not stale (r=%f)", self.url, r)
        return False

    def get_latest(self):
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
            logging.warn("Got %s when fetching %s", resp.status_code, self.url)
            return None

        doc = readability.Document(resp.text)
        title = doc.title()
        summary = doc.summary(html_partial=True)
        summary = bleach.clean(summary, tags=["p"], strip=True)
        summary = _normal(summary)

        # in case there was a redirect, and remove utm style marketing
        canonical_url = _remove_utm(resp.url)

        # get the latest version, if we have one
        from diffengine.model import EntryVersion
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
            # Stuff doesn't allow crawlers so wayback won't archive it
            # new.archive()
            if old:
                logging.debug("found new version %s", old.entry.url)
                # TODO Remove diff from this class
                # diff = Diff.create(old=old, new=new)
                # if not diff.generate():
                #     logging.warn("html diff showed no changes: %s", self.url)
                #     new.delete()
                #     new = None
            else:
                logging.debug("found first version: %s", self.url)
        else:
            logging.debug("content hasn't changed %s", self.url)

        self.checked = datetime.utcnow()
        self.save()

        return new

def _normal(s):
    # additional normalizations for readability + bleached text
    s = s.replace("\xa0", " ")
    s = s.replace('“', '"')
    s = s.replace('”', '"')
    s = s.replace("’", "'")
    s = s.replace("\n", " ")
    s = s.replace("­", "")
    s = re.sub(r'  +', ' ', s)
    s = s.strip()
    return s

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

def _equal(s1, s2):
    return _fingerprint(s1) == _fingerprint(s2)

punctuation = dict.fromkeys(i for i in range(sys.maxunicode)
        if unicodedata.category(chr(i)).startswith('P'))

def _get(url):
    return requests.get(url, timeout=60, headers={"User-Agent": UA})
