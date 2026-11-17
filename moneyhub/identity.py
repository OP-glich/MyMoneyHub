"""Local receipt identity. No developer's personal name is embedded in receipts."""
import os


def owner_name():
    return (os.environ.get('MYMONEYHUB_DISPLAY_NAME','Local Lender').strip() or 'Local Lender')[:100]


def lender_label():
    return owner_name()+' — OSI LOAN'
