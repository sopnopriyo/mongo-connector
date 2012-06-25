"""Tails the oplog of a shard and returns entries"""

import os
import time
import json
import pymongo

from bson.objectid import ObjectId
from bson.timestamp import Timestamp
from threading import Thread, Timer
from checkpoint import Checkpoint
from solr_doc_manager import SolrDocManager
from util import (get_namespace_details,
                  get_connection, 
                  get_next_document,
                  bson_ts_to_long,
                  long_to_bson_ts)


class OplogThread(Thread):
    """OplogThread gathers the updates for a single oplog. 
    """
    
    def __init__(self, primary_conn, mongos_address, oplog_coll, is_sharded,
     doc_manager, oplog_file, namespace_set):
        """Initialize the oplog thread.
        """
        Thread.__init__(self)
        self.primary_connection = primary_conn
        self.mongos_address = mongos_address
        self.oplog = oplog_coll
        self.is_sharded = is_sharded
        self.doc_manager = doc_manager
        self.running = False
        self.checkpoint = None
        self.oplog_file = oplog_file
        self.namespace_set = namespace_set 
        self.mongos_connection = get_connection(mongos_address)
        
    def run(self):
        """Start the oplog worker.
        """
        self.running = True  
        
        if self.is_sharded is False:
            print 'handle later'
            return
              
        while self.running is True:    
            
            cursor = self.prepare_for_sync()
            last_ts = None
            
            for entry in cursor:  
                print entry                           #debugging purposes
                operation = entry['op']

                if operation == 'd':
                    doc_id = entry['o']['_id']
                    self.doc_manager.remove(doc_id)
                
                elif operation == 'i' or operation == 'u':
                    doc = self.retrieve_doc(entry)
                    self.doc_manager.upsert([doc])
                    
                last_ts = entry['ts']
            
            if last_ts is not None:                 #we actually processed docs
                self.checkpoint.commit_ts = last_ts
                print 'writing TS because last_ts is not None'
                self.write_config()
                
            time.sleep(2)   #for testing purposes
            
    
    
    def stop(self):
        """Stop this thread from managing the oplog.
        """
        self.running = False
            
            
    def retrieve_doc(self, entry):
        """Given the doc ID's, retrieve those documents from the mongos.
        """
        namespace = entry['ns']
        doc_id = entry['o']['_id']

        db_name, coll_name = get_namespace_details(namespace)
        coll = self.mongos_connection[db_name][coll_name]
        doc = coll.find_one({'_id':doc_id})
      
        return doc
    
    def get_oplog_cursor(self, timestamp):
        """Move cursor to the proper place in the oplog. 
        """
        ret = None
        
        if timestamp is not None:
            cursor = self.oplog.find(spec={'op':{'$ne':'n'}, 
            'ts':{'$gt':timestamp}}, tailable=True, order={'$natural':'asc'})
          #  doc = get_next_document(cursor)     
            ret = cursor
            
        return ret
        
    def get_last_oplog_timestamp(self):
        """Return the timestamp of the latest entry in the oplog.
        """
        curr = self.oplog.find().sort('$natural',pymongo.DESCENDING).limit(1)
        return curr[0]['ts']
        
    #used here for testing, eventually we will use last_oplog_ts() + full_dump()
    def get_first_oplog_timestamp(self):
        """Return the timestamp of the latest entry in the oplog.
        """
        curr = self.oplog.find().sort('$natural',pymongo.ASCENDING).limit(1)
        return curr[0]['ts']
        
    
    def dump_collection(self):
        """Dumps collection into backend engine.
        
        This method is called when we're initializing the cursor and have no
        configs i.e. when we're starting for the first time.
        """
        for namespace in self.namespace_set:
            db, coll = get_namespace_details(namespace)
            cursor = self.primary_connection[db][coll].find()
            
            doc_list = []
            
            for doc in cursor:
                self.doc_manager.upsert(doc)
            
            
        
    def init_cursor(self):
        """Position the cursor appropriately.
        
        The cursor is set to either the beginning of the oplog, or wherever it was 
        last left off. 
        """
        timestamp = self.read_config()
         
        if timestamp is None:
            timestamp = self.get_last_oplog_timestamp()
            self.dump_collection()
            
        self.checkpoint.commit_ts = timestamp
        cursor = self.get_oplog_cursor(timestamp)
        
        return cursor
            
        
    def prepare_for_sync(self):
        """ Initializes the cursor for the sync method. 
        """
        cursor = None
        last_commit = None

        if self.checkpoint is None:
            self.checkpoint = Checkpoint()
            cursor = self.init_cursor()
        else:
            last_commit = self.checkpoint.commit_ts
            cursor = self.get_oplog_cursor(last_commit)
                    
            if cursor is None:
                cursor = self.init_cursor()
                
        return cursor
        
        
    def write_config(self):
        """
        Write the updated config to the config file. 
        
        This is done by duplicating the old config file, editing the relevant
        timestamp, and then copying the new config onto the old file. 
        """
        os.rename(self.oplog_file, self.oplog_file + '~')  # temp file
        dest = open(self.oplog_file, 'w')
        source = open(self.oplog_file + '~', 'r')
        oplog_str = str(self.oplog.database.connection)
        
        timestamp = bson_ts_to_long(self.checkpoint.commit_ts)
        json_str = json.dumps([oplog_str, timestamp])
        dest.write(json_str) 
            
        for line in source:
            if oplog_str in line:
                continue                        # we've already updated
            else:
                dest.write(line)
  
        
        source.close()
        dest.close()
        os.remove(self.oplog_file+'~')
        
        
        #need to consider how to get one global config file for all primary oplogs
    def read_config(self):
        """Read the config file for the relevant timestamp, if possible.
        """      
        config_file = self.oplog_file
        if config_file is None:
            print 'Need a config file!'
            return None
        
        source = open(self.oplog_file, 'r')
        try: 
            data = json.load(source)
        except:                                             # empty file
            return None
        
        oplog_str = str(self.oplog.database.connection)
        
        count = 0
        while (count < len(data)):
            if oplog_str in data[count]:                    #next line has time
                count = count + 1
                self.checkpoint.commit_ts = long_to_bson_ts(data[count])
                break
            count = count + 2                               # skip to next set
            
        return self.checkpoint.commit_ts
                
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
        
    