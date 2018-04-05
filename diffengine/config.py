import os
import subprocess

import feedparser
import logging

import sys
import tweepy
import yaml
from gunicorn._compat import FileNotFoundError
from peewee import SqliteDatabase

home = None


class Config:
    @staticmethod
    def  home_path(rel_path):
        return os.path.join(home, rel_path)

    @staticmethod
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

    @staticmethod
    def setup_logging():
        # path = config.get('log', '/var/log/diffengine.log')
        path = '/var/log/diffengine/diffengine.log'
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            filename=path,
            filemode="a"
        )
        logging.getLogger("readability.readability").setLevel(logging.WARNING)
        logging.getLogger("tweepy.binder").setLevel(logging.ERROR)

    @staticmethod
    def load_config(home, prompt=True):
        config_file = os.path.join(home, "config.yaml")
        if os.path.isfile(config_file):
            config_file = yaml.load(open(config_file))
        else:
            logging.debug("Prompting for config, config not found at %s", config_file)
            if not os.path.isdir(home):
                os.makedirs(home)
            if prompt:
                config_file = Config.get_initial_config()
            yaml.dump(config_file, open(config_file, "w"), default_flow_style=False)

        return config_file

    @staticmethod
    def setup_db(config):
        db = SqliteDatabase(None)
        db_file = config.get('db', config.home_path('diffengine.db'))
        logging.debug("connecting to db %s", db_file)
        db.init(db_file)
        db.connect()
        from diffengine.model import Feed, Entry, EntryVersion, Diff
        db.create_tables([Feed, Entry, EntryVersion, Diff], safe=True)

        return db

    @staticmethod
    def setup_phantomjs(config):
        phantomjs = config.get("phantomjs", "phantomjs")
        try:
            subprocess.check_output([phantomjs, '--version'])
            return phantomjs
        except FileNotFoundError:
            print("Please install phantomjs <http://phantomjs.org/>")
            print("If phantomjs is intalled but not in your path you can set the full path to phantomjs in your config: %s" % home.rstrip("/"))
            sys.exit()
