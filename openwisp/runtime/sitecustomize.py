"""Use the bundled suffix snapshot in every upstream dashboard subprocess."""

import tldextract

tldextract.extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
