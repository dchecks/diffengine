import logging

import tweepy
from datetime import datetime


class FeedProcessor:
    def __init__(self, twitter_consumer, tweeting=False, ):
        self.tweeting = tweeting
        self.twitter_consumer = twitter_consumer
        self.checked = 0
        self.skipped = 0
        self.new = 0
        self.diffs = 0
        self.tweeted = 0

    def process_feed_entries(self, entries, twitter_config, archive_enabled=False):
        # get latest content for each entry
        for entry in entries:
            if not entry.stale:
                self.skipped += 1
                logging.debug("%s - Skipping entry not stale", entry.id)
                continue
            self.checked += 1
            try:
                version = entry.get_latest(archive_enabled)
                if version:
                    self.new += 1
                if version and version.diff:
                    self.diffs += 1
                    if self.tweeting:
                        self.tweet_diff(version.diff, twitter_config)
                        self.tweeted += 1
            except Exception as e:
                logging.exception('Unable to get latest for entry %s', entry.id)

    def stats(self):
        return "new: {}, checked: {}, skipped: {}, diffs: {}, tweeted: {}".format(
                          self.new, self.checked, self.skipped, self.diffs, self.tweeted)

    def tweet_diff(self, diff, twitter_config):
        if not self.twitter_consumer:
            logging.debug("twitter consumer not configured")
            return
        elif not twitter_config:
            logging.debug("twitter access token/secret not set up for feed")
            return
        elif diff.tweeted:
            logging.warn("diff %s has already been tweeted", diff.id)
            return

        logging.info("tweeting about  %s", diff.new.title)
        auth = tweepy.OAuthHandler(self.twitter_consumer['consumer_key'], self.twitter_consumer['consumer_secret'])
        auth.secure = True
        auth.set_access_token(twitter_config['access_token'], twitter_config['access_token_secret'])
        twitter = tweepy.API(auth)

        status = diff.new.title
        status = status.replace('| Stuff.co.nz', '')

        if len(status) >= 225:
            status = status[0:225] + "…"

        status += ' ' + diff.new.url

        try:
            twitter.update_with_media(diff.thumbnail_path(), status)
            diff.tweeted = datetime.utcnow()
            logging.info("tweeted %s", status)
            diff.save()
        except Exception as e:
            logging.exception("unable to tweet")
