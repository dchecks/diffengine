from peewee import DateTimeField, DeferredRelation, BaseModel
from datetime import datetime



class FeedEntry(BaseModel):
    feed = DeferredRelation('Feed')
    entry = DeferredRelation('Entry')
    created = DateTimeField(default=datetime.utcnow())
