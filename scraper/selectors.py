"""
Centralized CSS / text hints for Amazon HTML (amazon.in).
Amazon markup changes often — adjust here only.
"""

from __future__ import annotations

# --- Search results ---
SEARCH_RESULT_ITEM = '[data-component-type="s-search-result"]'
SEARCH_TITLE_LINK = "h2 a.a-link-normal"
PAGINATION_NEXT = 'a.s-pagination-item[aria-label="Go to next page"]'

# --- Product detail ---
PRODUCT_TITLE = "#productTitle"
PRICE_WHOLE = "span.a-price-whole"
PRICE_FRACTION = "span.a-price-fraction"
PRICE_OFFSCREEN = "span.a-offscreen"
BUYBOX_PRICE = "#corePrice_feature_div span.a-offscreen"
BRAND_BYLINE = "#bylineInfo"
FEATURE_BULLETS = "#feature-bullets ul"

# Seller / buying options entry points (link text fragments are checked in code)
TEXT_SEE_ALL_BUYING = "see all buying options"
TEXT_OTHER_SELLERS = "other sellers on amazon"

# Offer listing page (multi-seller)
OFFER_ROW = "div.olpOffer"
OFFER_SELLER_NAME = ".olpSellerName"
OFFER_PRICE = "span.a-color-price"

# Images
MAIN_IMAGE = "#landingImage"
ALT_IMAGES_CONTAINER = "#altImages"
IMAGE_BLOCK = "#imageBlock"
