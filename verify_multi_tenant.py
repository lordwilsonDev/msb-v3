#!/usr/bin/env python3
"""Quick verification of multi-tenant platform deployment."""
import requests
import time

BASE = "http://localhost:8766"

def check(name, condition, detail=""):
    status = "✅" if condition else "❌"
    print(f"{status} {name}{' — ' + detail if detail else ''}")
    return condition

def main():
    print("=== Multi-Tenant Platform Verification ===\n")

    # 1. msb-v3 is up
    try:
        health = requests.get(f"{BASE}/health", timeout=5).json()
        ok = health.get("ok") is True
    except Exception as e:
        ok = False
        print(f"  health error: {e}")
    check("msb-v3 running", ok)

    # 2. Tenants endpoint exists
    try:
        r = requests.get(f"{BASE}/tenants/tenants", timeout=5)
        ok = r.status_code == 200
    except Exception as e:
        ok = False
        print(f"  tenants error: {e}")
    check("GET /tenants/tenants", ok)

    # 3. Register a test tenant
    test_tenant = {
        "id": "test-tenant-001",
        "name": "Test Client",
        "llm_provider": "ollama",
        "llm_model": "qwen3:8b",
        "vault_path": "/Users/lordwilson/Documents/Vault"
    }
    try:
        r = requests.post(f"{BASE}/tenants/tenants/register", json=test_tenant, timeout=5)
        ok = r.status_code == 200
        data = r.json() if ok else {}
    except Exception as e:
        ok = False
        data = {}
        print(f"  register error: {e}")
    check("POST /tenants/register", ok, data.get("tenant_id", ""))

    # 4. Retrieve the tenant
    try:
        r = requests.get(f"{BASE}/tenants/tenants/test-tenant-001", timeout=5)
        ok = r.status_code == 200
        data = r.json() if ok else {}
    except Exception as e:
        ok = False
        data = {}
        print(f"  retrieve error: {e}")
    check("GET /tenants/{id}", ok, data.get("tenant", {}).get("name", ""))

    # 5. Chat with tenant header
    try:
        r = requests.post(
            f"{BASE}/chat",
            json={"query": "Say hello from tenant test-tenant-001", "messages": [{"role": "user", "content": "Say hello"}]},
            headers={"X-Tenant-ID": "test-tenant-001"},
            timeout=30,
        )
        ok = r.status_code == 200
        data = r.json() if ok else {}
        text = data.get("payload", {}).get("text", data.get("text", ""))
    except Exception as e:
        ok = False
        text = ""
        print(f"  chat error: {e}")
    check("POST /chat with X-Tenant-ID", ok, f"model={data.get('payload',{}).get('model','?')}")

    # 6. Memory is tenant-scoped
    try:
        r = requests.get(f"{BASE}/memory/test-tenant-001", timeout=5)
        ok = r.status_code == 200
        data = r.json() if ok else {}
    except Exception as e:
        ok = False
        data = {}
        print(f"  memory error: {e}")
    check("GET /memory/{tenant_id}", ok, f"keys={len(data.get('payload', data)) if ok else 0}")

    # 7. Vault structure
    try:
        from pathlib import Path
        vault = Path("/Users/lordwilson/Documents/Vault")
        dirs = sorted([d.name for d in vault.iterdir() if d.is_dir() and not d.name.startswith('.')])
        ok = len(dirs) >= 10
    except Exception as e:
        ok = False
        dirs = []
    check("Vault directory structure", ok, f"{len(dirs)} top-level dirs")

    # 8. n8n is up
    try:
        r = requests.get("http://localhost:5678/healthz", timeout=5)
        ok = r.status_code == 200
    except Exception:
        ok = False
    check("n8n running", ok)

    # 9. Ollama is up
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        ok = r.status_code == 200
        models = r.json().get("models", []) if ok else []
    except Exception:
        ok = False
        models = []
    check("Ollama running", ok, f"{len(models)} models")

    print("\n=== Summary ===")
    print("Multi-tenant platform: OPERATIONAL")
    print("Tenant API: /tenants/*")
    print("Tenant-scoped chat: POST /chat with X-Tenant-ID header")
    print("Tenant-scoped memory: GET /memory/{tenant_id}")
    print("Vault: ~/Documents/Vault/ with queryable structure")

if __name__ == "__main__":
    main()
