from distutils.core import setup

setup(
    name='flickrsaver',
    version='0.1',
    description='A screensaver for Flickr enthusiasts',
    url='http://github.com/joh/Flickrsaver',
    license='BSD',
    author='Johannes H. Jensen',
    author_email='joh@pseudoberries.com',
    
    requires=[
        'clutter (>=1.0.3)',
        'flickrapi'
    ],
    
    # TODO: Absolute system paths like this should be avoided, but
    # unfortunately gnome-screensaver seems to only allow screensavers
    # which reside in a list of hard-coded system directories...
    data_files=[('/usr/lib/xscreensaver', ['flickrsaver.py']),
                ('share/applications/screensavers', ['flickrsaver.desktop'])]
)
