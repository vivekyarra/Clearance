"""Model-aware search ranking: pages naming the exact model come first, sibling models last."""
import pytest

from agents.catalog_builder.build import model_match, page_rank, search_urls, vendor_hosted


@pytest.mark.parametrize("url,title,model,rank", [
    ("https://www.bearrobotics.ai/servi", "Servi: x", "Servi", 0),
    ("https://www.bearrobotics.ai/servi-plus", "Servi Plus: x", "Servi", 2),
    ("https://www.bearrobotics.ai/servi-plus", "Servi Plus", "Servi Plus", 0),
    ("https://www.bearrobotics.ai/servi", "Servi: x", "Servi Plus", 2),
    ("https://www.pudurobotics.com/en/products/bellabotpro", "BellaBot Pro | Pudu", "BellaBot", 2),
    ("https://www.pudurobotics.com/en/products/bellabot", "BellaBot | Pudu", "BellaBot", 0),
    ("https://robotleasen.com/en/robots/pudu-bellabot-pro", "", "BellaBot", 2),
    ("https://www.keenon.com/en/product/t8", "", "DINERBOT T8", 0),
    ("https://ottomotors.com/100", "", "OTTO 100", 0),
    ("https://x.example/pudubot-2", "", "PuduBot 2", 0),
    ("https://x.example/pudubot", "", "PuduBot 2", 2),
    ("https://www.bearrobotics.ai/restaurants", "Restaurants", "Servi", 1),
])
def test_page_rank(url, title, model, rank):
    assert page_rank(url, title, model) == rank


def test_model_match_empty_inputs():
    assert model_match("", "Servi") == 1
    assert model_match("/servi", "") == 1


@pytest.mark.parametrize("url,vendor,ok", [
    ("https://www.keenon.com/en/product/T8", "Keenon Robotics", True),
    ("https://www.pudurobotics.com/en/products/bellabot", "Pudu Robotics", True),
    ("https://ottomotors.com/100", "OTTO Motors", True),
    ("https://somebots.com/en/robots/trays-t8/keenon-T8", "Keenon Robotics", False),
    ("https://www.robotsinternational.com/x", "Pudu Robotics", False),
])
def test_vendor_hosted(url, vendor, ok):
    assert vendor_hosted(url, vendor) is ok


class _Search:
    def __init__(self, results):
        self.results = results

    def search(self, q, max_results=8):
        return {"results": self.results}


def test_search_urls_prefers_exact_model_over_sibling_and_score():
    t = _Search([
        {"url": "https://www.bearrobotics.ai/servi-plus", "title": "Servi Plus", "score": 0.95},
        {"url": "https://reseller.example/bear-servi", "title": "Bear Servi specs", "score": 0.5},
        {"url": "https://www.bearrobotics.ai/servi", "title": "Servi", "score": 0.6},
        {"url": "https://www.bearrobotics.ai/restaurants", "title": "Restaurants", "score": 0.9},
    ])
    assert search_urls(t, "Bear Robotics", "Servi", max_urls=4) == [
        "https://www.bearrobotics.ai/servi", "https://reseller.example/bear-servi",
        "https://www.bearrobotics.ai/restaurants", "https://www.bearrobotics.ai/servi-plus"]
