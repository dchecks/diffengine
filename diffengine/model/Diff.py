import codecs
import datetime
from datetime import time
import os

import logging
import re

from django.template.backends import jinja2
from peewee import ForeignKeyField, DateTimeField
from selenium import webdriver

from diffengine.model import BaseModel
from diffengine.model import EntryVersion
from diffengine.config import home_path, config
from setup import htmldiff


class Diff(BaseModel):
    old = ForeignKeyField(EntryVersion, related_name="prev_diffs")
    new = ForeignKeyField(EntryVersion, related_name="next_diffs")
    created = DateTimeField(default=datetime.utcnow)
    tweeted = DateTimeField(null=True)
    blogged = DateTimeField(null=True)

    @property
    def html_path(self):
        # use prime number to spread across directories
        path = home_path("diffs/%s/%s.html" % ((self.id % 257), self.id))
        if not os.path.isdir(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))
        return path

    @property
    def screenshot_path(self):
        return self.html_path.replace(".html", ".jpg")

    @property
    def thumbnail_path(self):
        return self.screenshot_path.replace('.jpg', '-thumb.jpg')

    def generate(self):
        if self._generate_diff_html():
            self._generate_diff_images()
            return True
        else:
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

    def _generate_diff_html(self):
        if os.path.isfile(self.html_path):
            return
        tmpl_path = os.path.join(os.path.dirname(__file__), "diff_template.html")
        logging.debug("creating html diff: %s", self.html_path)
        diff = htmldiff.render_html_diff(self.old.html, self.new.html)
        if not self.validate_diff(diff):
            return False
        tmpl = jinja2.Template(codecs.open(tmpl_path, "r", "utf8").read())
        html = tmpl.render(
            title=self.new.title,
            url=self.old.entry.url,
            old_time=self.old.created,
            new_time=self.new.created,
            diff=diff
        )
        codecs.open(self.html_path, "w", 'utf8').write(html)
        return True

    def _generate_diff_images(self):
        if os.path.isfile(self.screenshot_path):
            return
        if not hasattr(self, 'browser'):
            phantomjs = config.get('phantomjs', 'phantomjs')
            self.browser = webdriver.PhantomJS(phantomjs)
        logging.debug("creating image screenshot %s", self.screenshot_path)
        self.browser.set_window_size(1400, 1000)
        self.browser.get(self.html_path)
        time.sleep(5) # give the page time to load
        self.browser.save_screenshot(self.screenshot_path)
        logging.debug("creating image thumbnail %s", self.thumbnail_path)
        self.browser.set_window_size(800, 400)
        self.browser.execute_script("clip()")
        self.browser.save_screenshot(self.thumbnail_path)

    # From EntryVersion
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

    # From EntryVersion
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
