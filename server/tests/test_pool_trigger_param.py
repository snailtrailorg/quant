"""池定向回补端点回归（批16 bug4 修复的修复——盲审 B-P0）。

病情：pool_id 注解 int 而 pools.id/pool_symbols.pool_id 是 TEXT（迁移 0018）——
  ① 文本池 ID（实值如 test-pool）→ FastAPI 参数解析 422；
  ② 数字串 ID → 转 int 后 WHERE pool_id=%s 打 text 列 → PG text=int 500。
双路皆死 = bug4「池回补分钟」按钮修完依旧不可用。修法=注解改 str。

判别式：带伪 Authorization 头（缺头本身也 422，与参数错混淆）——伪头下鉴权拦 401/403；
若响应 422 且 detail 指名 pool_id，即 int 注解回归。
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from src.web_api.main import app
    return TestClient(app)

HEADERS = {"Authorization": "Bearer dummy-token-pool-trigger-regression"}


def _pool_id_rejected(r):
    """422 且错误定位在 pool_id 参数 = int 注解回归。"""
    if r.status_code != 422:
        return False
    try:
        detail = r.json().get("detail")
    except Exception:
        return False
    if not isinstance(detail, list):
        return False
    return any(str(e.get("loc", [])[-1:]) == "['pool_id']" or "pool_id" in e.get("loc", []) for e in detail)


class TestPoolTriggerPoolId:
    def test_text_pool_id_param_accepted(self, client):
        """文本池 ID 不再 422-on-pool_id（int 注解回归即红）。"""
        r = client.post("/api/sync/pool-data/trigger?pool_id=test-pool", headers=HEADERS)
        assert not _pool_id_rejected(r), f"pool_id 又变回 int 注解：{r.text[:200]}"

    def test_numeric_string_pool_id_param_accepted(self, client):
        """数字串池 ID 同样合法（str 注解不预转换，杜绝 PG text=int）。"""
        r = client.post("/api/sync/pool-data/trigger?pool_id=123", headers=HEADERS)
        assert not _pool_id_rejected(r), f"pool_id 类型回归：{r.text[:200]}"

    def test_no_pool_id_still_valid(self, client):
        """不传 pool_id（全池触发）不受影响。"""
        r = client.post("/api/sync/pool-data/trigger", headers=HEADERS)
        assert not _pool_id_rejected(r), f"无参路径回归：{r.text[:200]}"
