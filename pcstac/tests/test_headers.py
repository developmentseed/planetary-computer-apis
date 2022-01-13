def test_cors_enabled(app_client) -> None:
    """
    When the request supplies an origin header (as a browser would), ensure
    that the response has an `access-control-allow` header, set to all origins.
    """
    expected_header = "access-control-allow-origin"
    response = app_client.get("/", headers={"origin": "http://example.com"})
    assert response.status_code == 200
    assert expected_header in response.headers
    assert response.headers[expected_header] == "*"
