from app.models import GeoCache
from app.services.geocode import geocode_area, geocode_place


def fetcher(results, counter):
    def fetch(q):
        counter.append(q)
        return results
    return fetch


R = [{"display_name": "Third Wave Coffee, 12th Main, Indiranagar", "lat": "12.97", "lon": "77.64"}]


def test_confirmed_and_cached(session):
    calls = []
    a = geocode_place(session, "Third Wave Coffee", "Indiranagar, Bengaluru", fetcher(R, calls))
    b = geocode_place(session, "third wave coffee", "indiranagar, bengaluru", fetcher(R, calls))
    assert a == (12.97, 77.64, "confirmed") and a == b and len(calls) == 1


def test_rejected_when_names_differ(session):
    other = [{"display_name": "Municipal Water Tank, Indiranagar", "lat": "1", "lon": "2"}]
    assert geocode_place(session, "Third Wave Coffee", "Bengaluru", fetcher(other, []))[2] == "rejected"


def test_no_candidates(session):
    assert geocode_place(session, "Tiny Shack", "Gokarna", fetcher([], []))[2] == "no_candidates"


def test_error_is_not_cached(session):
    def boom(q):
        raise RuntimeError("down")
    assert geocode_place(session, "X Cafe", "Y", boom)[2] == "error"
    assert session.query(GeoCache).count() == 0


def test_area_centre_is_approx(session):
    assert geocode_area(session, "Gokarna", fetcher([{"lat": "14.54", "lon": "74.31"}], [])) == (14.54, 74.31, "approx")


def test_blank_input_is_error(session):
    assert geocode_place(session, "", "x")[2] == "error"
    assert geocode_area(session, "")[2] == "error"
