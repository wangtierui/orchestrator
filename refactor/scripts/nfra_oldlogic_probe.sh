#!/bin/bash
# 临时探针：照搬旧架构 modules/regulatory_scrapers/collectors/nfra/fetch_lists.py 的 curl 逻辑
# （固定 UA=Chrome/124 + cookie jar + 首页 warmup 种 cookie + Referer），
# 用来和重构后的 python-requests+随机UA 路径做对照，判断封锁是纯 IP 层还是指纹/UA 层。
set -u
API="https://www.nfra.gov.cn/cbircweb"
LIST="$API/DocInfo/SelectDocByItemIdAndChild"
REF="https://www.nfra.gov.cn/cn/view/pages/ItemList.html?itemPId=923&itemId=926&itemUrl=ItemListRightMore.html&itemName=%E6%94%BF%E7%AD%96%E6%B3%95%E8%A7%84"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
CK="$(mktemp /tmp/nfra_ck.XXXXXX.txt)"

echo "[1/2] warmup: 首页种 cookie"
curl -s -L -A "$UA" -c "$CK" --max-time 30 \
  "https://www.nfra.gov.cn/cn/view/pages/index/index.html" \
  -o /dev/null -w "      warmup http=%{http_code}\n"

echo "[2/2] list: 928 列表（curl + cookie jar + Referer）"
RESP="$(curl -s -L -A "$UA" -H "Referer: $REF" -b "$CK" -c "$CK" --max-time 30 \
  -w $'\nHTTP_STATUS=%{http_code}' \
  "$LIST?itemId=928&pageSize=18&pageIndex=1")"
echo "$RESP" | tail -1
echo "      body前200字节: $(echo "$RESP" | head -c 200)"

rm -f "$CK"
