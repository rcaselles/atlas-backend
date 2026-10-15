import os

# Never hit the internet from tests.
os.environ["ATLAS_NEWS_POLLING"] = "false"
os.environ["ATLAS_MARKET_STREAMING"] = "false"
