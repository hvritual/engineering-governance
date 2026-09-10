# CG-00：外置治理执行章程

> **R4 公开发布说明（2026-09-10）**：用户已明确授权将这四份治理文档发布至 `hvritual/engineering-governance` 的当前公开仓库。以下正文是 R3 归档；其中“仅私有交付／尚未上传”的叙述仅表示 R3 历史，不再构成公开发布阻断。消费者执行资格仍为 **BLOCKED**，CG-01 尚未启动。R4 使用 GitHub 文件接口发布文档快照，原始本地 Git 历史保存在交付 bundle；远端提交号不冒充原提交号。最终文件摘要与远端回读以 R4 交付回执为准。授权不扩大到源码、日志、凭据、CI 或权限修改。

状态：**R3 已恢复 R2 历史并核实治理远端：可读，但为公开空仓库。源码／工具链及发布边界仍阻断；CG-00 为 BLOCKED，未启动 CG-01。**

编制日期：2026-09-10。时间记录使用 UTC。依据：用户批准的《CG：外置工程治理与持续修复实施路线》CG-00 卡；输入文件 SHA-256 为 `9cb24b0b811d71eae1658f338f8781e76fdc0a4ecdb2ff425d1f89ef5e971084`。

## 1. 本轮决定与交付

本轮只冻结治理资产位置、事实基线、改动边界和命令前置条件。从R2 bundle恢复独立的未发布云端 Git 工作目录 `/mnt/data/cg00-r3/engineering-governance`，不向 Yunka 注入治理产品能力。仅产生四个受版本管理的文件：

| 文件 | 作用 |
|---|---|
| docs/CG-CHARTER.md | 本轮范围、决策、接受边界和阻断条件 |
| docs/BASELINES.json | 三仓主线、消费者源码锁、声明依赖、工具与实际执行状态 |
| docs/PATH-OWNERSHIP.md | 本轮精确写范围、所有权交集、全局拒绝项 |
| docs/COMMAND-CATALOG.md | 命令来源、工作目录、前置条件、写入风险和执行状态 |

原始探测输出、源文件摘录、提交回执、校验清单和交付包位于 `/mnt/data/cg00-evidence` 及同级产物目录，均不写进被验证的治理源码树。没有新 CLI、规则引擎、Makefile、脚手架或业务实现。

## 2. 治理资产位置及远端边界

G 本轮工作位置为 `/mnt/data/cg00-r3/engineering-governance`，原附件不覆盖。R2之前的404只作为历史保留。R3通过GitHub连接器读取 `hvritual/engineering-governance` 成功：repository id `1364383040`，`private=false`、`visibility=public`、`permissions.push=true`。`refs/heads/main` 返回409及 `Git Repository is empty.`，所以不能把默认分支名称main当作已有提交。

**远端存在／可读已经核实；私有发布边界没有满足。**权限字段不是公开发布授权，也不是本轮写入测试。本轮没有修改仓库可见性，没有上传文档、Git blob、Issue或PR，没有配置本地origin。原始治理资料与bundle继续仅作会话私有交付。

用户将仓库设为并核实私有，或明确修改这批文档的公开发布边界后，才可继续远端交付。即使解决可见性，消费者实际资格仍须补齐，不能把远端可读提升为CG-00 DONE。创建空README或API新根提交会偏离现有真实Git历史，本轮没有这样做。

依据：仓库元数据 `https://api.github.com/repos/hvritual/engineering-governance`；main查询 `https://api.github.com/repos/hvritual/engineering-governance/git/ref/heads/main`。外部证据 `connector-observations-r3.json` 是连接器返回值的选定字段记录，不声称为逐字原始响应。

## 3. 事实源与版本冻结（保留R1任务基线）

| 角色 | Commit | Tree |
|---|---|---|
| Yunka 框架参考 main | cc546c7d25ee316442eafacd3c93599f7691f22a | 84ef1130c3aead3fef83b990951790b6d996d2a0 |
| Biz main | fed9051bae618ab28df200038682f808c82e854b | 5883f6783dbab1336fa25b36adb1f7e938086540 |
| IoT Delivery main | bcd20632b666405c3f7a8fe8d53f591c78450087 | 0c0d1a2da23de4b7315245faba2103ec7a64377e |

以上是R1冻结、R2保留的 GitHub 连接器远端事实；R3未自动切换三仓基线，不是本地 `git rev-parse` 输出。三次源码克隆均因 DNS 失败，故本轮没有消费者工作树的 clean 判定。

### Biz

Biz `.yunka/source.env` 固定 `33b98ceba57494abda2299e4f0290a5651dab4bc`，对应 Yunka tree `fcc154390dc3a24ebb1819954fc883d0dd073c31`；Go 声明为 `v0.0.0-20260910080749-33b98ceba574`。然而 `go.mod` 使用相邻 `../yunka.io/` 的本地 replace，真正的加载结果必须由既有 `scripts/verify-yunka-source.sh` 和 `go list -m` 验证。不能把 require 行当作实际已加载版本。[B_LOCK][B_MOD][B_SOURCE_CHECK]

Makefile 中生成与检查均从 `$(YUNKA_ROOT)/app` 执行 `go run ./cmd`；CE-08 工作流同样锁定 `33b98ceba57494abda2299e4f0290a5651dab4bc`。本轮冻结的是配置要求，未取得已运行二进制的摘要。`YUNKA_ROOT` 可覆盖，故必须检查其与 replace 目标实际一致。[B_MAKE][B_CLOUD]

### IoT Delivery

`third_party/yunka` 的 Git tree 条目为 mode `160000`，commit `057ebcf88a87303eb633eb6e604d306f633dfac0`，该 commit tree 为 `b2d82c574f6db1950b4323db839feda2d6fbc898`。Makefile 对 CLI 与生成器也固定此源码，并使用该子模块自己的 `go.work`。[I_SUBMODULE][I_MAKE]

`backend-yunka` 声明 `github.com/hvritual/yunka.io/* v0.1.0`，实际指向本地子模块；legacy `backend` 则使用 `yunka.io/framework` 的零值伪版本及本地 replace。两套模块身份按原文保留，本轮没有运行模块解析来证明兼容性。[I_MOD_YUNKA][I_MOD_LEGACY]

根 `go.work` 明确包含 `backend` 与 `backend-yunka`；正常 YU-30 后端检查使用 `GOWORK=off`。单独检查 `backend-yunka` 不证明 legacy `backend` 已覆盖。[I_WORK][I_CLOUD]

### 三类版本不得合并

框架参考、消费者 Runtime、消费者生成器、审计 CLI 分开记录。当前消费者的源码锁都不同于框架 main；不能把 main 的 AG 能力自动视为旧消费者 CLI 已具备。安装态 CLI、实际加载模块和消费者 context/help 仍是未知／未验收，不用框架文档补齐这些空值。

## 4. 责任分离

Yunka 保留其原有契约、生成、Ownership 和变更验证职责。外部检测、修复配方及专用脚手架可以独立发布，不要求融入 Yunka。外部资产版本元数据不是第二份架构事实源；但不能复制、改写 canonical Application、OperationPlan 或 Ownership 作为另一套权威。

CG-00 不进行工具准入。ruleguard、ast-grep、独立 Analyzer、Copier、Codemod、Renovate 仅沿用路线中的候选位置，未安装、未采纳、未验证兼容性。不得提前把 CG-02/08/09 写成完成。

## 5. R1 执行历史（保留，不作为 R2 新结果）

| 项目 | 实际结果 |
|---|---|
| 三仓 refs 与关键配置读取 | 已通过 GitHub 连接器完成，绑定固定 SHA |
| 两个消费者锁定的框架 commit 对象 | 已回读 Git commit/tree |
| 本地 Git 可用 | 2.47.3 |
| 本地 Go | 1.23.2；不满足两个消费者要求的 1.25.13 |
| 三仓克隆 | 退出 128，`Could not resolve host: github.com` |
| PATH 中的 Yunka CLI | 不存在；`--help` 和 `context --json` 前置探测退出 127 |
| 消费者级 context / help | 未运行；上述前置探测不是消费者 context 结果 |
| 源码 worktree HEAD/tree/status | 未运行，因为源码未物化 |
| 业务、框架、CI、权限变更 | 无 |
| GitHub 治理远端 | 未建立／未推送／未回读 |

已有 GitHub Actions 配置已经读取，但它们的历史成功记录不是本轮成功；本轮没有触发远程运行，也没有获得新的 run ID。相关证据仅用于冻结既有执行入口，不代替命令实测。[B_CLOUD][I_CLOUD]

### R2 恢复执行结果（历史）

R1 输入包 29 个清单文件均通过 SHA-256 核对；从原 Git bundle 恢复治理提交 `209eba25fa8c576b43f102099c9fc07cf7d83a12`，tree `37494e8dcc0328f9905af4fe870a19d8d5120716`。这仅恢复 G，不将零散文件或空目录冒充消费者源码。

本轮发现并修正 `CG00-DOC-01`：命令目录的 tree 查询含错误 revision；旧命令实际退出 128，改为 `git rev-parse --verify 'HEAD^{tree}'` 后返回正确 tree。上轮 31 项文档校验没有执行目录中的 Git 命令；本轮从文档提取命令实际回放，并加入错对象、缺命令、额外命令、脏树及带空格路径的反例。仅返回零退出码也不足以通过对象身份核验。

本轮 GitHub 回读：Yunka 与 IoT head 未变；Biz main 已变为 `12f33db5632a81c04b9de7634b5d37baf5a2bb1a`，tree `717b9d562cc5e477f46155706778cd10b9fd5a52`。本任务继续使用原冻结 Biz `fed9051bae618ab28df200038682f808c82e854b`；新主线只记入 `remote_ref_observations`，不自动重置消费者版本。重新定基线须另记决定，并完整重采集，不能混用新源码与旧回执。

来源：R2 GitHub refs 及 Biz 精确 commit 对象（均为只读）：
- https://api.github.com/repos/hvritual/yunka.io/git/ref/heads/main
- https://api.github.com/repos/hvritual/biz/git/ref/heads/main
- https://api.github.com/repos/hvritual/biz/git/commits/12f33db5632a81c04b9de7634b5d37baf5a2bb1a
- https://api.github.com/repos/hvritual/iot-delivery-system/git/ref/heads/main

环境阻断未解除：当前 DNS 探测 github.com 失败，curl 对 github.com/api.github.com 均退出 6；Go 仍为1.23.2，PATH 无 Yunka/protoc，常见位置未找到1.25.13。可用源码下载/云端执行路径检索没有获得源码、工具链或新 run ID；不重跑无前提的消费者命令，不修改消费者 CI 制造入口。治理远端候选再次返回404，仍未建立远端。

### R3 实際执行与变化

R2交付包46个清单文件摘要全部匹配；恢复父提交 `25c8721f99dacae5f7f6ae4f320695fae3198720`，tree `18cf52c52994569931058b79d94163b0db26d23a`，继续分支 `cg-00/resume-r3`。仅写原四份文档。R2的Git身份命令保留并继续实际重放，不新增CG-01规则或治理产品代码。

容器实际结果：github.com的getent退出2，curl退出6；go.dev的curl退出6；Go为1.23.2，PATH无Yunka/protoc。固定消费者命令仍未运行。本轮没有把R1的clone=128或CLI=127记为新执行。

查到IoT既有源码导出run `33969592332`，artifact `9970509351`。元数据显示其已于 `2026-09-06T13:40:49Z` 过期，绑定head `d6241a26dcdb268f002a38376689d893600f1877` 而不是冻结的 `bcd20632...`。未下载、未运行新任务，未用旧run成功代替验收。该观察仅覆盖这一候选制品，不断言所有云端制品都不存在。

## 6. 阻断及解除条件

**CG00-ENV-01：完整消费者源码仍未物化。**需要固定SHA的完整Git checkout或可核验bundle；核验commit、tree、工作树及两个消费者不同的框架副本。已过期或身份不匹配的制品不能代替。

**CG00-ENV-02：固定工具链与CLI仍未实际运行。**需要Go1.25.13及锁定源码、依赖；先运行身份／context／help／模块解析，保持消费者pin不变。不能通过Go1.23.2、latest工具或文字证明替代。

**CG00-DELIVERY-01：可达性已解除，发布边界与执行前提未解除。**远端当前已知为公开空仓库，而已冻结要求为私有。未经明确边界变更不公开上传；不自行改权限或可见性。可见性合格、交付可执行后保留真实提交历史同步并回读main。

三项条件分别跟踪；仓库存在不清除环境阻断，文档校验通过不清除消费者阻断。本轮整体保持BLOCKED，CG-01未启动。

## 7. 本轮验收与后续边界

文档本身可做 JSON 解析、必填字段、来源映射、版本一致性、docs-only diff、Git blob 摘录校验、提交完整性、bundle 重放检查。这些文档验收即使通过，也不提升为消费者资格。

恢复前禁止消费本记录中的 `null` 实际身份作为默认 main；禁止将工作流定义当成云端命令已执行；禁止用 narrow PASS 消除未检查范围；禁止让外部回执伪装成旧 Yunka 原生 Attestation。

一轮完整 DONE 仍要求：真实核验 → 文档更新 → Git 提交 → 获准远端同步/集成 → 实际 main 回读。当前只交付文档与未发布云端提交，缺项保留。R3新提交以R2 `25c8721f...` 为父提交，不重建无历史的替代G。后续基线复用必须验证实际 commit/tree/文件摘要，而非只看状态文字。


## 证据索引

本文 `[ID]` 指 BASELINES.json 的 sources 条目；全部绑定固定提交。部分小文件的原文、SHA-256 和 Git blob 核对记录见交付包 evidence/source-snapshots。其余是本轮连接器读取的固定源引用，不冒充本地 Git checkout。

- [B_MOD] `hvritual/biz@fed9051bae61` / `go.mod` — https://github.com/hvritual/biz/blob/fed9051bae618ab28df200038682f808c82e854b/go.mod
- [B_WORK] `hvritual/biz@fed9051bae61` / `go.work` — https://github.com/hvritual/biz/blob/fed9051bae618ab28df200038682f808c82e854b/go.work
- [B_LOCK] `hvritual/biz@fed9051bae61` / `.yunka/source.env` — https://github.com/hvritual/biz/blob/fed9051bae618ab28df200038682f808c82e854b/.yunka/source.env
- [B_MAKE] `hvritual/biz@fed9051bae61` / `Makefile` — https://github.com/hvritual/biz/blob/fed9051bae618ab28df200038682f808c82e854b/Makefile
- [B_SOURCE_CHECK] `hvritual/biz@fed9051bae61` / `scripts/verify-yunka-source.sh` — https://github.com/hvritual/biz/blob/fed9051bae618ab28df200038682f808c82e854b/scripts/verify-yunka-source.sh
- [B_PROJECT] `hvritual/biz@fed9051bae61` / `.yunka/project.json` — https://github.com/hvritual/biz/blob/fed9051bae618ab28df200038682f808c82e854b/.yunka/project.json
- [B_CLOUD] `hvritual/biz@fed9051bae61` / `.github/workflows/ce08-qualification.yml` — https://github.com/hvritual/biz/blob/fed9051bae618ab28df200038682f808c82e854b/.github/workflows/ce08-qualification.yml
- [B_TOOLCHAIN] `hvritual/yunka.io@33b98ceba574` / `tools/toolchain.env` — https://github.com/hvritual/yunka.io/blob/33b98ceba57494abda2299e4f0290a5651dab4bc/tools/toolchain.env
- [I_MOD] `hvritual/iot-delivery-system@bcd20632b666` / `go.mod` — https://github.com/hvritual/iot-delivery-system/blob/bcd20632b666405c3f7a8fe8d53f591c78450087/go.mod
- [I_WORK] `hvritual/iot-delivery-system@bcd20632b666` / `go.work` — https://github.com/hvritual/iot-delivery-system/blob/bcd20632b666405c3f7a8fe8d53f591c78450087/go.work
- [I_SUBMODULE] `hvritual/iot-delivery-system@bcd20632b666` / `.gitmodules` — https://github.com/hvritual/iot-delivery-system/blob/bcd20632b666405c3f7a8fe8d53f591c78450087/.gitmodules
- [I_MOD_YUNKA] `hvritual/iot-delivery-system@bcd20632b666` / `backend-yunka/go.mod` — https://github.com/hvritual/iot-delivery-system/blob/bcd20632b666405c3f7a8fe8d53f591c78450087/backend-yunka/go.mod
- [I_MOD_LEGACY] `hvritual/iot-delivery-system@bcd20632b666` / `backend/go.mod` — https://github.com/hvritual/iot-delivery-system/blob/bcd20632b666405c3f7a8fe8d53f591c78450087/backend/go.mod
- [I_MAKE] `hvritual/iot-delivery-system@bcd20632b666` / `backend-yunka/Makefile` — https://github.com/hvritual/iot-delivery-system/blob/bcd20632b666405c3f7a8fe8d53f591c78450087/backend-yunka/Makefile
- [I_PROJECT] `hvritual/iot-delivery-system@bcd20632b666` / `.yunka/project.json` — https://github.com/hvritual/iot-delivery-system/blob/bcd20632b666405c3f7a8fe8d53f591c78450087/.yunka/project.json
- [I_CLOUD] `hvritual/iot-delivery-system@bcd20632b666` / `.github/workflows/yu30-regression.yml` — https://github.com/hvritual/iot-delivery-system/blob/bcd20632b666405c3f7a8fe8d53f591c78450087/.github/workflows/yu30-regression.yml
- [I_REGRESSION] `hvritual/iot-delivery-system@bcd20632b666` / `backend-yunka/scripts/run-yu30-regression.sh` — https://github.com/hvritual/iot-delivery-system/blob/bcd20632b666405c3f7a8fe8d53f591c78450087/backend-yunka/scripts/run-yu30-regression.sh
