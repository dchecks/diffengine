import os
import sys
import tweepy
import logging

from datetime import datetime

from config import Config
from model import Feed


class DiffEngine:

    def __init__(self, config, db):
        self.config = config
        self.db = db

    def check_feeds(self):
        checked = skipped = new = 0

        for f in self.config.get('feeds', []):
            feed, created = Feed.create_or_get(url=f['url'], name=f['name'])
            if created:
                logging.debug("created new feed for %s", f['url'])

            # get latest feed entries
            feed.refresh_feed()

            # get latest content for each entry
            for entry in feed.entries:
                if not entry.stale:
                    skipped += 1
                    continue
                checked += 1
                try:
                    version = entry.get_latest()
                except Exception as e:
                    logging.error('unable to get latest', e)
                    continue
                if version:
                    new += 1
                if version and version.diff and 'twitter' in f:
                    self.tweet_diff(version.diff, f['twitter'])
        logging.info("Feed checking completed, new=%s checked=%s skipped=%s", new, checked, skipped)

    def tweet_diff(self, diff, token):
        if 'twitter' not in self.config:
            logging.info("twitter not configured")
            return
        elif not token:
            logging.info("access token/secret not set up for feed")
            return
        elif diff.tweeted:
            logging.warn("diff %s has already been tweeted", diff.id)
            return

        logging.info("tweeting about  %s", diff.new.title)
        t = self.config['twitter']
        auth = tweepy.OAuthHandler(t['consumer_key'], t['consumer_secret'])
        auth.secure = True
        auth.set_access_token(token['access_token'], token['access_token_secret'])
        twitter = tweepy.API(auth)

        status = diff.new.title
        status = status.replace('| Stuff.co.nz', '')

        if len(status) >= 85:
            status = status[0:85] + "…"

        status += ' ' + diff.new.url

        try:
            twitter.update_with_media(diff.thumbnail_path, status)
            diff.tweeted = datetime.utcnow()
            logging.info("tweeted %s", status)
            diff.save()
        except Exception as e:
            logging.error("unable to tweet: %s", e)


def init(new_home, prompt=True):
    home = new_home

    cObj = Config()

    # Config.setup_logging(config)
    Config.setup_logging()
    config = Config.load_config(home, prompt)
    Config.setup_phantomjs(config)
    db = Config.setup_db(config)

    return DiffEngine(config, db)


def main():
    if len(sys.argv) == 1:
        home = os.getcwd()
    else:
        home = sys.argv[1]

    diff_engine = init(home)
    start_time = datetime.utcnow()
    logging.info("starting up with home=%s", home)
    diff_engine.check_feeds()

    elapsed = datetime.utcnow() - start_time
    logging.info("shutting down, elapsed=%s", elapsed)


def _dt(d):
    return d.strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    main()
