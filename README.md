# 跨站智能消防装备资料的可撤销实体归并资料源

本项目保存本地合成资料及对应的来源站行为，供资料接入开发使用。内容取自虚构场景，不代表真实活动、人员、设施或设备参数。来源服务只模拟公开读取与指定的表单行为，不包含资料采集端。

## 环境与运行

使用 Python 3.11 及以上版本，仅依赖标准库。来源只监听本机地址，端口可以通过参数调整。两个终端分别启动不同端口可以表示两个独立来源。

```sh
python -m source_site --port 8090
```

`source_site/source.py` 管理来源响应，`source_site/__main__.py` 是 HTTP 适配层，`sources/` 保存原文与接口约定。状态只存在来源进程内，重启来源会恢复初始样本。`/__control` 和 `/__calls` 属于本地运维入口，资料接入端的正常操作不使用它们。运维端可以通过 POST `/__control` 提供 JSON 对象改变 phase 或更新 state。phase 列表与业务接口见下文，细节见 `sources/contract.json`。

- `GET /index`：返回资料页地址
- `page_fields`：data-record-id站内身份，data-kind可信度，data-revision来源修订
- `phases`：["base", "withdrawn"]
- `decisions`：来源kind与来源id用冒号连接形成引用

用 Python 的 HTTP 客户端推进来源状态，例如：

```sh
python -c 'import urllib.request; r=urllib.request.Request("http://127.0.0.1:8090/__control", data=b"{\"phase\":\"base\"}", headers={"Content-Type":"application/json"}); print(urllib.request.urlopen(r).read().decode())'
```

## 开发检查

以下检查覆盖来源协议、异常响应和真实本地 HTTP 启动。检查不会要求访问公网，也不会写入业务状态目录。

```sh
python -m unittest discover -s tests -v
python -m compileall -q .
```

容器环境也可以复现相同检查：

```sh
docker build -t equipment-identity-source .
docker run --rm --network none equipment-identity-source
```

来源的HTTP异常是合成协议行为，便于资料接入端开发和诊断。样本可扩展；业务数据、原始HTML以及接口响应不应被当作真实部署凭据或真实业务事实。

## 资料接入端 equipment_identity

`equipment_identity/` 是采集与实体归并模块（仅标准库），完成「采集 → 规范化候选 → 人工决定 → 实体导出 → 证据链查询」全流程。核心原则：原始观察与人工决定只追加、不覆盖；实体、候选、合成参数等一切派生状态都是「原始记录 + 生效决定」的纯函数，可随时离线重算，且结果不依赖导入顺序。

### 使用方式

```sh
python -m source_site --port 8090 &                      # 启动本地来源
python -m equipment_identity --data data collect --source http://127.0.0.1:8090
python -m equipment_identity --data data import-decisions sources/decisions.json
python -m equipment_identity --data data status          # 记录/实体/候选/冲突总览
python -m equipment_identity --data data export --out entities.json
python -m equipment_identity --data data evidence E-xxx  # 实体证据链（每项参数的出处）
python -m equipment_identity --data data evidence maker:a
python -m equipment_identity --data data lineage E-xxx   # 拆分/归并沿革
python -m equipment_identity --data data recompute       # 仅从原始记录重算，不请求网页
```

### 规则摘要

- **采集**：每次抓取生成新观察版本（`observations.jsonl` + `raw/` 原文），永不覆盖；每个字段保留选择器与起止偏移，可回溯原始页面确切位置。撤稿（410）也留下观察，只撤掉对应证据。
- **实体**：只能由「完全一致的非空厂商编码 + 规范化型号」或生效的人工 same 决定支持；名称相似只产生候选。全角型号、连字符变体等做机械规范化（NFKC、大写、统一连字符），旧商标不做语义猜测。
- **决定**：`decision_id` 幂等，重复导入不重复执行；同号不同内容拒绝覆盖。`revoke` 撤销先前确认；目标未到时作为待生效撤销接受，保证顺序无关。`different` 是不可绕过的约束：合并证据链与互斥决定冲突时整个组件待处理并解释，不按导入顺序选边。
- **合成**：按字段执行，厂商页 > 简报 > 转载；同级取较新 data-revision；同级同修订值冲突保留并列值。保留值与被覆盖值都带出处。
- **沿革**：实体标识由成员集合内容寻址，成员不变则标识不变；拆分/归并/出现/消失写入 `lineage.jsonl`，已导出标识可查询。

### 回归检查

```sh
python -m unittest discover -s tests -v
```

`tests/test_pipeline.py` 覆盖：同名异厂保持分开、无厂商转载停留候选区、别名撤销后恢复、重复决定幂等、全部导入排列结果一致、互斥冲突整体待处理、撤稿只撤对应证据并在恢复后还原、同级同修订并列值、字段位置可回溯。
