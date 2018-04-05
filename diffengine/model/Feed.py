import datetime
import logging

import feedparser
from peewee import CharField, DateTimeField, ForeignKeyField

from model.BaseModel import BaseModel
from model import Entry
from model import FeedEntry


class Feed(BaseModel):
    url = CharField(primary_key=True)
    name = CharField()

    # entries = ForeignKeyField(Entry, related_name='feed_entry')

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
            resp = Entry._get(self.url)
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

            if created in dupe_table:
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
