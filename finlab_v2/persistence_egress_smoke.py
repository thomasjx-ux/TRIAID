from pathlib import Path
from threading import RLock

from triaid_fin import store as store_module


class FakeBackend:
    def __init__(self):
        self.root=Path('/remote/fake')
        self.persistent=True
        self.durability='PERSISTENT'
        self.list_names_calls=0
        self.list_texts_calls=0
        self.read_text_calls=0
        self.exists_calls=0
        self.saved={}

    def status(self):
        return {'backend':'supabase','persistent':True}

    def path(self,name):
        return self.root/name

    def list_names(self,prefix,suffix=''):
        self.list_names_calls+=1
        return ['runs/US-one.json','runs/HK-two.json']

    def list_texts(self,prefix,suffix=''):
        self.list_texts_calls+=1
        raise AssertionError('status must not download run contents')

    def read_text(self,name):
        self.read_text_calls+=1
        if name=='missing.json':
            raise FileNotFoundError(name)
        return '{"ok":true}'

    def exists(self,name):
        self.exists_calls+=1
        raise AssertionError('load_json must not do exists + read double round-trip')

    def atomic_write_text(self,name,text):
        self.saved[name]=text

    def append_line(self,name,line):
        pass

    def read_lines(self,name,limit=None):
        return []


def main():
    fake=FakeBackend()
    original=store_module.build_storage_backend
    store_module.build_storage_backend=lambda root=None: fake
    try:
        store=store_module.RunStore()
        first=store.status()
        second=store.status()
        assert first['run_count']==2 and second['run_count']==2
        assert fake.list_names_calls==1, fake.list_names_calls
        assert fake.list_texts_calls==0, fake.list_texts_calls

        value=store.load_json('state.json')
        assert value=={'ok':True}, value
        missing=store.load_json('missing.json',{'default':True})
        assert missing=={'default':True}, missing
        assert fake.read_text_calls==2, fake.read_text_calls
        assert fake.exists_calls==0, fake.exists_calls
        print('PERSISTENCE_EGRESS_SMOKE_OK', first['status_semantics'])
    finally:
        store_module.build_storage_backend=original


if __name__=='__main__':
    main()
