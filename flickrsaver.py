#!/usr/bin/env python
"""
Flickr Saver: Screensaver

Flickr Saver downloads interesting photos from Flickr and displays them
as a screensaver.

Requires pyclutter >= 1.0.2 for clutter.x11

Keep a cache of X photos in ~/.cache/flickrsaver/
Name photos by their Flickr ID
Every time a photo is shown, delete it from the pool and fetch a random new one
- Only delete it *after* a new one has been successfully fetched (in case of no network connection)

Pool:
    add(id, url, info)
    remove(id)
    get(id=None)
    
Producer:
    next()
    
"""
import flickrapi
import glib
import gobject
import clutter
import clutter.x11
import urllib2
import time
import os
import logging
from random import randint
from threading import Thread, Event, Condition, RLock

gobject.threads_init()
clutter.threads_init()

log = logging.getLogger('flickrsaver')
log.setLevel(logging.DEBUG)

API_KEY = "59b92bf5694c292121537c3a754d7b85"
flickr = flickrapi.FlickrAPI(API_KEY)

""" Where we keep photos """
cache_dir = os.path.join(glib.get_user_cache_dir(), 'flickrsaver')

class PhotoSource(object):
    def get_photo(self, target_dir):
        """ Get a new photo from the source and place it in target_dir,
            returning the filename """
        raise NotImplementedError


class PhotoPool(Thread):
    """ A pool of photos! """
    
    def __init__(self, num_photos=10, sources=[], pool_dir=cache_dir):
        Thread.__init__(self)
        
        self.num_photos = num_photos
        self.sources = sources
        self.dir = pool_dir
        
        # Make sure cache dir exists
        if not os.path.exists(self.dir):
            os.mkdir(self.dir)
        
        # Load cached photos
        self.photos = os.listdir(self.dir)
        
        # Delete queue
        self.trash = []
        
        # Condition when a new photo is added
        self.added = Condition()
        
        # Condition when a photo is removed
        self.removed = Condition()
        
        # Event for stopping the pool
        self._stop = Event()
    
    def add_source(self, source):
        self.sources.append(source)
    
    def is_empty(self):
        return len(self.photos) == 0
    
    def is_full(self):
        return len(self.photos) >= self.num_photos
        
    def add(self, filename):
        """ Add a photo to the pool """
        with self.added:
            self.photos.append(filename)
            self.added.notifyAll()
    
    def pop(self, filename=None):
        """ Pop a photo from the pool
        
        If filename is not set, a random photo will be returned
        """
        if not self.photos and self.trash:
            # Recycle
            log.debug("Recycling...")
            self.add(self.trash.pop(0))
        
        while not self.photos and not self._stop.is_set():
            with self.added:
                # Wait for a photo to be produced
                self.added.wait(0.1)
        
        if self._stop.is_set():
            return None
        
        # TODO: filename arg?
        with self.removed:
            r = randint(0, len(self.photos) - 1)
            p = self.photos.pop(r)
            self.removed.notify()
            log.debug("Photo %s consumed", p)
            
            return p
    
    def delete(self, filename):
        """ Mark file as deleted """
        self.trash.append(filename)
        '''
        if os.path.isabs(filename):
            assert os.path.dirname(filename) == cache_dir
        else:
            filename = os.path.join(self.dir, filename)
        
        os.remove(filename)
        '''
    
    def run(self):
        src = 0
        
        assert len(self.sources) > 0
        
        while not self._stop.is_set():
            
            if self.is_full():
                with self.removed:
                    self.removed.wait(0.1)
                    
            if not self.is_full():
                source = self.sources[src]
                
                try:
                    filename = source.get_photo(self.dir)
                    self.add(filename)
                except Exception as e:
                    log.warning("Source '%s' failed: %s", source, e)
                    time.sleep(1)
                
                # Next source
                src = (src + 1) % len(self.sources)
            
            # Empty trash
            while self.trash and len(self.photos) + len(self.trash) > self.num_photos:
                f = self.trash.pop()
                log.debug("Deleting %s...", f)
                os.remove(os.path.join(self.dir, f))
       
        log.debug("Pool stopped")
            
    
    def stop(self):
        log.info("Stopping pool...")
        self._stop.set()


class FlickrSource(PhotoSource):
    def __init__(self, refresh=30):
        """ Refresh every 30 secs """
        PhotoSource.__init__(self)
        
        self.results = None
        self.refresh = refresh
        self.last_refresh = None
    
    def get_tree(self):
        raise NotImplementedError()
    
    def get_photo(self, target_dir):
        if not self.results or time.time() - self.last_refresh >= self.refresh:
            log.debug("Downloading list...")
            tree = self.get_tree()
            self.results = tree.find('photos').findall('photo')
            self.last_refresh = time.time()
        
        url = None
        while not url:
            r = randint(0, len(self.results) - 1)
            p = self.results.pop(r)
            
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
        
        u = urllib2.urlopen(url)
        filename = os.path.join(target_dir, os.path.basename(url))
        f = open(filename, 'wb')
        f.write(u.read())
        
        return filename

class Interestingness(FlickrSource):
    def get_tree(self):
        #return flickr.photos_search(user_id='7353466@N08', extras='url_s,url_m,url_o', per_page=500)
        return flickr.interestingness_getList(extras='url_s,url_m,url_o', per_page=500)
    
    def __repr__(self):
        return 'Interestingness()'

class Photostream(FlickrSource):
    def __init__(self, user_id):
        FlickrSource.__init__(self)

        self.user_id = user_id
        
    def get_tree(self):
        return flickr.people_getPublicPhotos(user_id=self.user_id, extras='url_s,url_m,url_o', per_page=500)
    
    def __repr__(self):
        return 'Photostream(%r)' % (self.user_id)

class Group(FlickrSource):
    def __init__(self, group_id):
        FlickrSource.__init__(self)

        self.group_id = group_id
        
    def get_tree(self):
        return flickr.groups_pools_getPhotos(group_id=self.group_id, extras='url_s,url_m,url_o', per_page=500)
    
    def __repr__(self):
        return 'Group(%r)' % (self.group_id)
    
class PhotoUpdater(Thread):
    def __init__(self, saver, photo_pool, interval=5):
        Thread.__init__(self)
        
        self.saver = saver
        self.photo_pool = photo_pool
        self.interval = interval
        
        self._stop = Event()
    
    def run(self):
        ts = 0
        
        while not self._stop.is_set():
            if time.time() - ts >= self.interval:
                log.debug("Updater: Next!")
                p = self.photo_pool.pop()
                filename = os.path.join(self.photo_pool.dir, p)
                self.saver.set_photo(filename, None)
                ts = time.time()
            
            time.sleep(0.1)
        
        log.debug("Updater stopped")
    
    def stop(self):
        log.debug("Stopping updater...")
        self._stop.set()
        

class FlickrSaver(object):
    def __init__(self, photo_sources=[]):
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
        self.photo1.hide()
        self.stage.add(self.photo1)
        
        self.photo2 = clutter.Texture()
        self.photo2.hide()
        self.stage.add(self.photo2)
        
        self.photo = self.photo2
        
        # Animation
        self.timeline = clutter.Timeline(duration=2000)
        self.alpha = clutter.Alpha(self.timeline, clutter.EASE_IN_CUBIC)
        self.fade_in = clutter.BehaviourOpacity(0, 255, self.alpha)
        self.fade_out = clutter.BehaviourOpacity(255, 0, self.alpha)
        
        self.stage.show_all()
        
        # Photo pool
        self.photo_pool = PhotoPool()
        
        # Photo sources
        for ps in photo_sources:
            self.photo_pool.add_source(ps)
        
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
        
        try:
            # TODO: auto-rotate (based on EXIF info)
            self.photo.set_from_file(self.filename)
            self.scale_photo()
            
            self.fade_in.remove_all()
            self.fade_out.remove_all()
            self.fade_in.apply(self.photo)
            self.fade_out.apply(prev)
            self.timeline.rewind()
            self.timeline.start()
            
        except glib.GError as e:
            log.warning("Could not load photo: %s", e)
            self.photo = prev
        
        finally:
            # Finished, clear update_id
            self.update_id = 0
            
            # Mark file for deletion
            if self.filename:
                self.photo_pool.delete(self.filename)
            
            return False
    
    def queue_update(self):
        """ Queue an update of the graph """
        if not self.update_id:
            # No previous updates pending
            self.update_id = gobject.idle_add(self.update)
    
    def set_photo(self, filename, info):
        self.filename = filename
        self.queue_update()
    
    def scale_photo(self):
        width, height = self.stage.get_size()
        ow, oh = self.photo.get_base_size()
        w = ow
        h = oh
        
        log.debug("scale_photo: Stage: %sx%s, Photo: %sx%s", width, height, ow, oh)
        
        if ow > width or oh > height:
            w = width
            h = oh * w / ow
            if h > height:
                h = height
                w = ow * h / oh
            
            log.debug("Downscaling photo from %sx%s to %sx%s", ow, oh, w, h)
            
        self.photo.set_size(w, h)
        
        # Center
        cx = int(width / 2 - w / 2)
        cy = int(height / 2 - h / 2)
        log.debug("Centering %sx%s to stage %sx%s: (%s, %s)", w, h, width, height, cx, cy)
        self.photo.set_position(cx, cy)
    
    def size_changed(self, *args):
        width, height = self.stage.get_size()
        
        log.debug("Stage size: %dx%d", width, height)
        
        # Resize photo
        self.scale_photo()
    
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
        
        self.updater.join()
        self.photo_pool.join()
        
        clutter.main_quit()


if __name__ == '__main__':
    import argparse
    
    '''
    if 'XSCREENSAVER_WINDOW' in os.environ:
        f = open('/tmp/foo', 'w')
        f.write('XSCREENSAVER_WINDOW=' + os.environ['XSCREENSAVER_WINDOW'] + '\n')
        f.close()
    '''
    
    # Parse command-line arguments
    #Photostream('7353466@N08')
    parser = argparse.ArgumentParser(description='A screensaver for Flickr enthusiasts')
    
    parser.add_argument('-u', '--user', action='append', default=[], metavar='USER_ID',
                        help="Show photos from user's Photostream")
    parser.add_argument('-g', '--group', action='append', default=[], metavar='GROUP_ID',
                        help="Show photos from group's Photostream")
    parser.add_argument('-i', '--interesting', action='store_true',
                        help="Show interesting photos from the last 7 days")
    
    parser.add_argument('-d', '--days', type=int,
                        help="Only show photos newer than the specified number of days")
    
    args = parser.parse_args()
    
    photo_sources = []
    
    # User's photostream
    for user_id in args.user:
        source = Photostream(user_id)
        photo_sources.append(source)
    
    # Group's photostream
    for group_id in args.group:
        source = Group(group_id)
        photo_sources.append(source)
    
    # Default: Interestingness
    if args.interesting or not photo_sources:
        source = Interestingness()
        photo_sources.append(source)
    
    # Fire up the screensaver
    fs = FlickrSaver(photo_sources)
    fs.main()
    