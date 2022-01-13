def test_get_collections(app_client):
    """Test read /collections"""
    resp = app_client.get("/collections")
    assert resp.status_code == 200


def test_get_collection(app_client):
    """Test read a collection which does not exist"""
    resp = app_client.get("/collections/naip")
    assert resp.status_code == 200


def test_collection_not_found(app_client):
    """Test read a collection which does not exist"""
    resp = app_client.get("/collections/does-not-exist")
    assert resp.status_code == 404
