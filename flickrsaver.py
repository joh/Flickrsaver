#!/usr/bin/env python
"""
Flickr Saver: Screensaver

Flickr Saver downloads interesting photos from Flickr and displays them
as a screensaver.
"""
import flickrapi
import gobject
import clutter
import clutter.x11
import urllib2
import logging
import tempfile
import shutil
import time
import os
from threading import Thread, Event, Condition, RLock

gobject.threads_init()
clutter.threads_init()

log = logging.getLogger('flickrsaver')
log.setLevel(logging.DEBUG)

API_KEY = "59b92bf5694c292121537c3a754d7b85"
flickr = flickrapi.FlickrAPI(API_KEY)


class FlickrPool(Thread):
    """ Thread which fetches photos from Flickr """
    
    def __init__(self, num_photos=2):
        Thread.__init__(self)
        
        self.num_photos = num_photos
        self.tmpdir = tempfile.mkdtemp(prefix='flickrsaver')
        self.photos = []
        self.page = 0
        
        # Condition when a new photo is ready
        self._produced = Condition()
        
        # Condition when a photo is consumed
        self._consumed = Condition()
        
        # Event for stopping the fetcher
        self._stop = Event()
    
    def get_photo(self):
        """ Get a photo from the pool """
        if not self.photos:
            with self._produced:
                self._produced.wait()
        
        with self._consumed:
            p = self.photos.pop(0)
            self._consumed.notify()
            log.debug("Photo #%s consumed", p[1].attrib['id'])
            return p
    
    def run(self):
        results = []
        
        while not self._stop.is_set():
            if len(results) == 0:
                # Fetch next page
                self.page += 1
                log.debug("Fetching page #%d...", self.page)
                r = flickr.interestingness_getList(extras='url_s,url_m,url_o',
                                                   page=self.page,
                                                   per_page=5)
                                                   
                results = r.find('photos').findall('photo')
            
            if len(self.photos) >= self.num_photos:
                time.sleep(1)
                continue
                
            if len(self.photos) < self.num_photos:
                p = results.pop(0)
                
                try:
                    url = p.attrib['url_o']
                except KeyError:
                    url = p.attrib['url_m']
                except KeyError:
                    url = p.attrib['url_s']
                except KeyError:
                    log.warn("No suitable URL found for photo #%s", p.attrib['id'])
                    continue
                
                log.debug("Downloading %s...", url)
                
                filename = os.path.join(self.tmpdir, os.path.basename(url))
                f = open(filename, 'wb')
                u = urllib2.urlopen(url)
                f.write(u.read())
                
                with self._produced:
                    log.debug("Photo #%s produced", p.attrib['id'])
                    self.photos.append((filename, p))
                    self._produced.notify()
        
        log.debug("Removing temporary directory %s...", self.tmpdir)
        shutil.rmtree(self.tmpdir)
    
    def stop(self):
        log.info("Stopping fetcher...")
        self._stop.set()

class PhotoUpdater(Thread):
    def __init__(self, saver, photo_pool, interval=5):
        Thread.__init__(self)
        
        self.saver = saver
        self.photo_pool = photo_pool
        self.interval = interval
        
        self._stop = Event()
    
    def run(self):
        while not self._stop.is_set():
            log.debug("Updater: Next!")
            filename, info = self.photo_pool.get_photo()
            self.saver.set_photo(filename, info)
            time.sleep(self.interval)
    
    def stop(self):
        self._stop.set()
        

class FlickrSaver(object):
    def __init__(self):
        # Set up Clutter stage and actors
        self.stage = clutter.Stage()
        self.stage.set_title('Flickr Saver')
        self.stage.set_color('#000000')
        self.stage.set_size(400, 400)
        self.stage.set_user_resizable(True)
        self.stage.connect('destroy', self.quit)
        self.stage.connect('notify::allocation', self.size_changed)
        self.stage.connect('key-press-event', self.key_pressed)
        
        
        if 'XSCREENSAVER_WINDOW' in os.environ:
            xwin = int(os.environ['XSCREENSAVER_WINDOW'], 0)
            clutter.x11.set_stage_foreign(self.stage, xwin)
        
        self.photo1 = clutter.Texture()
        self.stage.add(self.photo1)
        
        self.photo2 = clutter.Texture()
        self.stage.add(self.photo2)
        
        self.photo = self.photo2
        
        # Animation
        self.timeline = clutter.Timeline(duration=2000)
        self.alpha = clutter.Alpha(self.timeline, clutter.EASE_IN_CUBIC)
        self.fade_in = clutter.BehaviourOpacity(0, 255, self.alpha)
        self.fade_out = clutter.BehaviourOpacity(255, 0, self.alpha)
        
        self.stage.show_all()
        
        # Photo pool
        self.photo_pool = FlickrPool()
        
        # Photo updater
        self.updater = PhotoUpdater(self, self.photo_pool)
        
        # Update queueing
        self.update_id = 0
        self.filename = None
        
#        gobject.timeout_add_seconds(5, self.next_photo)
    
    def update(self):
        """ Update actors to new photo
        
        Note: must not be called from other than the main thread!
        """
        log.debug("Displaying %s", self.filename)
        
        prev = self.photo
        if self.photo == self.photo1:
            self.photo = self.photo2
        else:
            self.photo = self.photo1
        
        self.photo.set_from_file(self.filename)
        self._scale_photo()
        
        self.fade_in.remove_all()
        self.fade_out.remove_all()
        self.fade_in.apply(self.photo)
        self.fade_out.apply(prev)
        self.timeline.rewind()
        self.timeline.start()
        
        # Finished, clear update_id
        self.update_id = 0
        
        return False
    
    def queue_update(self):
        """ Queue an update of the graph """
        if not self.update_id:
            # No previous updates pending
            self.update_id = gobject.idle_add(self.update)
    
    def set_photo(self, filename, info):
        self.filename = filename
        self.queue_update()
    
    def _scale_photo(self):
        width, height = self.stage.get_size()
        ow, oh = self.photo.get_base_size()
        
        if ow and oh:
            w = width
            h = oh * width / ow
            if h > height:
                h = height
                w = ow * height / oh
                
            self.photo.set_size(w, h)
            self.photo.set_position(width / 2 - w / 2, height / 2 - h / 2)
    
    def size_changed(self, *args):
        width, height = self.stage.get_size()
        
        log.debug("Stage size: %dx%d", width, height)
        
        # Resize photo
        self._scale_photo()
    
    def key_pressed(self, stage, event):
        if event.keyval == clutter.keysyms.space:
            log.debug("NEXT PHOTO!")
            self.next_photo()
    
    def main(self):
        self.photo_pool.start()
        self.updater.start()
        clutter.main()
    
    def quit(self, *args):
        log.info("Exiting...")
        self.updater.stop()
        self.photo_pool.stop()
        clutter.main_quit()


if __name__ == '__main__':
    if 'XSCREENSAVER_WINDOW' in os.environ:
        f = open('/tmp/foo', 'w')
        f.write('XSCREENSAVER_WINDOW=' + os.environ['XSCREENSAVER_WINDOW'] + '\n')
        f.close()
        
    fs = FlickrSaver()
    fs.main()
    '''
    stage = clutter.Stage()
    stage.set_title('Flickr Saver')
    stage.set_color('#000000')
    stage.set_size(400, 400)
    stage.set_user_resizable(True)
    stage.connect('destroy', clutter.main_quit)
    width, height = stage.get_size()
    
    
    img = clutter.Texture(filename='/home/joh/Pictures/Kablam.jpg')
    w, h = img.get_size()
    h = h * width / w
    w = width
    img.set_size(w, h)
    img.set_position(0, height / 2 - h / 2)
    stage.add(img)
    
    def size_changed(*args):
        width, height = stage.get_size()
        w, h = img.get_size()
        h = h * width / w
        w = width
        img.set_size(w, h)
        img.set_position(0, height / 2 - h / 2)
        print "New size: %dx%d" % stage.get_size()
    
    stage.connect('notify::width', size_changed)
    
    stage.show_all()
    clutter.main()
    '''
    """
    photos = flickr.interestingness_getList(extras='url_s,url_m,url_o')
    
    for p in photos.find('photos').findall('photo'):
        print p.attrib['url_m']
    """
