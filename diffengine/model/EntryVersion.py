from datetime import datetime
from peewee import CharField, DateTimeField, ForeignKeyField, DeferredRelation

from diffengine.model import BaseModel


class EntryVersion(BaseModel):
    title = CharField()
    url = CharField()
    summary = CharField()
    created = DateTimeField(default=datetime.utcnow())
    # archive_url = CharField(null=True)
    # entry = ForeignKeyField(Entry, related_name='versions')
    entry = DeferredRelation('Entry')


    @property
    def html(self):
        return "<h1>%s</h1>\n\n%s" % (self.title, self.summary)

    # TODO Feature flag this
    # def archive(self):
    #     save_url = "https://web.archive.org/save/" + self.url
    #     try:
    #         resp = _get(save_url)
    #         wayback_id = resp.headers.get("Content-Location")
    #         if wayback_id:
    #             self.archive_url = "https://wayback.archive.org" + wayback_id
    #             logging.debug("archived version at %s", self.archive_url)
    #             self.save()
    #             return self.archive_url
    #         else:
    #             logging.error("unable to get archive id from %s: %s",
    #                     self.archive_url, resp.headers)
    #
    #     except Exception as e:
    #         logging.error("unexpected archive.org response for %s: %s", save_url, e)
    #     return None
