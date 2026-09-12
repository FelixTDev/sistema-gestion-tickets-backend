def test_health_endpoint_returns_api_status(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ticket-management-api"}
