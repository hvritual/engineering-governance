# CG-00 路径与所有权冻结

> **R4 公开发布说明（2026-09-10）**：用户已明确授权将这四份治理文档发布至 `hvritual/engineering-governance` 的当前公开仓库。以下正文是 R3 归档；其中“仅私有交付／尚未上传”的叙述仅表示 R3 历史，不再构成公开发布阻断。消费者执行资格仍为 **BLOCKED**，CG-01 尚未启动。R4 使用 GitHub 文件接口发布文档快照，原始本地 Git 历史保存在交付 bundle；远端提交号不冒充原提交号。最终文件摘要与远端回读以 R4 交付回执为准。授权不扩大到源码、日志、凭据、CI 或权限修改。

状态：**R3继续仅写四份G文档；消费者动态Ownership未运行，公开远端不属于已批准发布位置。**

## 1. 实际位置与精确 allowlist

G = `/mnt/data/cg00-r3/engineering-governance`。本轮允许修改的受版本管理路径只有：

```text
docs/CG-CHARTER.md
docs/BASELINES.json
docs/PATH-OWNERSHIP.md
docs/COMMAND-CATALOG.md
```

G 的 `.git` 由原始 CG-00 Git bundle 恢复，用于保留既有提交历史，不是治理产品实现。原始输出、源摘录、校验结果、patch、bundle 不写进 G 的源码树。未列出的新规则、代码、模板、工具锁、CI 和配置文件默认禁止。

| 对象 | CG-00 权限 | 说明 |
|---|---|---|
| F: hvritual/yunka.io | 只读 | 不升级 CLI、生成器、Runtime；固定消费者源码副本同样只读 |
| B: hvritual/biz | 只读 | 不改 Go 依赖、源码锁、业务、generated、工作流 |
| I: hvritual/iot-delivery-system | 只读 | 包含 backend、backend-yunka、web 与 third_party |
| I/server/** | 永久拒绝写入 | 即便该路径未在本轮根树中出现，用户保留规则仍有效 |
| I/third_party/yunka | 拒绝更换 pin／内容 | 子模块不能替换为当前框架 main |
| G/docs/ 上述四文件 | 可写 | 本轮唯一产品交付范围 |
| 产物目录 | 可写执行证据 | 位于审计根外；不是消费者源码 |

所有变更先匹配本轮精确任务授权，再交叉核对工具文件所有权和 Yunka Ownership（适用时），全局拒绝优先。未知 Ownership 不推定可写。CG-00 的 F/B/I allowlist 是空集合。

## 2. 已观察到的项目路径，不等于可写权限

| 消费者 | 原始项目配置 | 记录值 |
|---|---|---|
| Biz | contract.sources | contracts/sources.json |
| Biz | contract.generated | contracts/generated |
| Biz | modules.root | modules |
| Biz | generatedGo.root | internal |
| IoT | contract.protoRoot | backend-yunka/contracts/proto |
| IoT | contract.generated | backend-yunka/contracts/generated |
| IoT | modules.root | backend-yunka/modules |
| IoT | generatedGo.root | backend-yunka/internal |

来源：[B_PROJECT][I_PROJECT]。其中 `generatedGo.root` 是配置中的输出/布局根，**不把整个 internal 自动归为 generated-only，也不自动授权手写文件**。精确分类需真实执行各消费者锁定 CLI 的 Ownership；本轮未得到该快照。

`server/**` 的保护不得被 `backend/**` 迁移任务扩展解释。后续 CG-05/11 若产生业务修复，应另签单一消费者任务并固定精确路径，不能直接继承本轮 G 文档授权。

## 3. 云端布局约束

下列是恢复执行时的相对布局约束，**不是本轮已存在的源码 checkout**：

```text
<CLOUD_ROOT>/
  governance/engineering-governance/   # G
  reference/yunka.io/                 # 框架参考 main，不供 Biz 替代解析
  consumers/
    biz/                             # B，固定 fed9051bae61
    yunka.io/                        # Biz 相邻源码，固定 33b98ceba574
    iot-delivery-system/              # I，固定 bcd20632b666
      third_party/yunka/             # 固定 057ebcf88a87
  artifacts/cg00/                    # 审计根外的日志/临时产物/回执
  caches/                            # 隔离缓存；不是消费者依赖文件
```

Biz 的相邻 replace 是当前仓库的真实解析约束，不是复用某台桌面电脑路径。每次执行由云端布局明确提供该目录并验证 HEAD/tree。当前 `scripts/verify-yunka-source.sh` 使用 `-d "$YUNKA_ROOT/.git"`，所以普通 Git checkout 是该脚本的既有前置条件；不可假设 `.git` 为文件的 linked worktree 一定可用。[B_SOURCE_CHECK]

IoT 自己的框架副本不可被 Biz 的相邻副本替代。源文件摘录仅用于证据，不得填充到上述路径冒充整个项目。

## 4. 数据与依赖隔离

禁止治理工具修改消费者 `go.mod/go.sum/go.work/go.work.sum` 或前端锁文件；更不能通过临时 workspace 静默改变加载模块。实际解析身份目前未知，必须先做 `go list -m` 前后对比，方可在后续工具准入中声称零污染。

Biz `make generate` 会调用 `go mod tidy` 和商业目录写入，IoT `yunka-generate` 会写生成产物；均不是 CG-00 的只读命令。[B_MAKE][I_MAKE]

源代码检查不带 push/deploy 凭据，不挂载无关宿主目录或 Docker socket；本轮没有安装治理工具或执行外部模板任务。原始证据保存在当前云端产物目录，不公开提交。

## 5. 专用脚手架的后续准入规则

允许按真实问题引入其他脚手架。新脚手架须独立声明它管理的精确文件，与 Yunka 管理范围不重叠；模板更新不能接管业务实现或覆盖 Yunka generated。如果现有规则无法认可独立范围，使用相邻独立工程或仓库，而不是放宽 Yunka。

以上只是已批准路线的边界，不是 CG-08 已完成。当前没有 Copier/Codemod 配置，也没有第二生成器或模板 registry。

## 6. R3 恢复材料与当前写入核验

R2完整输入保存在 `/mnt/data/cg00-r3/r2-input`；本轮环境探测、文档命令回归及连接器观察位于 `/mnt/data/cg00-r3/evidence`。这些一次性校验程序不进入G提交树，也不是CG-01或CG-04实现。只从真实R2 bundle恢复G；没有消费者源码checkout，故不能报告F/B/I的git status已clean。

远端 `hvritual/engineering-governance` 已可读，但 `private=false`／`visibility=public`。`permissions.push=true`只描述连接器返回的权限，不授权发布当前私有治理文档。main查询409说明空仓库，不是“仓库仍不存在”。

**本轮远端写入allowlist为空**：不上传文档、blob、bundle、Issue或PR，不修改可见性和权限，不借改工作流触发云端命令。私有远端条件满足或公开发布边界经用户明确变更后，仍需另行核对实际上传范围和历史；对消费者的只读边界不随之改变。

本轮四文档记录的是最新事实与未解除条件，不放宽原验收。源码恢复所需工具、网络与可见性属于独立前提，不将其失败认定为Yunka/Biz/IoT业务缺陷。

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
