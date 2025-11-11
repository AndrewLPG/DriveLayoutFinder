from setuptools import setup

APP = ['drive_layout_finder.py']
OPTIONS = {
    'argv_emulation': True,
    'packages': [
        'pdf2image',
        'PIL',
        'imagehash',
        'googleapiclient',
        'google_auth_oauthlib',
        'google_auth_httplib2'
    ],
}

setup(
    app=APP,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
