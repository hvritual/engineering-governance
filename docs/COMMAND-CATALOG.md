# CG-00 命令目录与执行状态

> **R4 公开发布说明（2026-09-10）**：用户已明确授权将这四份治理文档发布至 `hvritual/engineering-governance` 的当前公开仓库。以下正文是 R3 归档；其中“仅私有交付／尚未上传”的叙述仅表示 R3 历史，不再构成公开发布阻断。消费者执行资格仍为 **BLOCKED**，CG-01 尚未启动。R4 使用 GitHub 文件接口发布文档快照，原始本地 Git 历史保存在交付 bundle；远端提交号不冒充原提交号。最终文件摘要与远端回读以 R4 交付回执为准。授权不扩大到源码、日志、凭据、CI 或权限修改。

**状态：R3保留R2修复的Git命令并重放；真实消费者命令验收仍为BLOCKED。远端读取成功不是远端发布完成。**

本文件区分 `EXECUTED_ENV_PROBE`、`SOURCE_CONFIRMED_NOT_RUN`、`DERIVED_INVOCATION_NOT_RUN` 与 `DEFERRED_WRITES`。前两者也不能互相替代：环境探测成功不等于消费者测试通过，工作流存在不等于本轮已经运行。

## 1. 环境探测历史与本轮恢复

以下 ENV-01 至 ENV-08 是 R1 历史，不表示 R2 重跑了这些命令。

| ID | 命令 | 实际结果 | 证明范围 |
|---|---|---|---|
| ENV-01 | git --version | 0；git version 2.47.3 | 本地 Git 工具存在 |
| ENV-02 | go version | 0；go1.23.2 linux/amd64 | 低于消费者要求；不是合格工具链 |
| ENV-03 | command -v git/go/yunka/gh/docker/protoc | 仅 git/go 有路径 | 无现成 Yunka/protoc |
| ENV-04/05/06 | git clone --no-checkout --filter=blob:none 三仓 | 全部128；DNS失败 | 源码无法物化；不是空仓库 |
| ENV-07 | yunka --help | 127；command not found | CLI 前提未满足 |
| ENV-08 | yunka context --json | 127；command not found | 不是固定消费者的 context 响应 |

ENV-07/08 的 CWD 是 `/tmp/cg00`，没有消费者工作树。这是探测缺失可执行文件，不伪装成在消费者根目录执行。完整 argv/CWD/时间/退出码/stdout/stderr 保存在 `evidence/environment-probes.json`。

### R2 实际复验（历史）

Git 仍为 2.47.3，Go 仍为 1.23.2。`getent ahosts github.com` 退出 2；对 github.com 与 api.github.com 的 curl 探测分别退出 6。PATH 无 Yunka/protoc，已检查的常见工具链位置没有 Go 1.25.13。没有因此重复执行无消费者工作树的 `yunka context`，更没有用 Go 1.23.2 构建消费者来替代资格。

本轮只在真实恢复的 G 仓库上复现并修正 `CG00-DOC-01`：旧 tree 表达式退出 128；正确表达式退出 0，返回原提交的 tree `37494e8dcc0328f9905af4fe870a19d8d5120716`，对象类型为 tree。原始输出见 `restore-and-red-green.json`。**这不是消费者源码验证。**

GitHub workflow/下载能力检索没有产生可用源码或新 run ID；治理远端候选再次 404。原始容器探测与连接器标准化观察分别保存在 R2 外部证据目录，不混为同一种证据。

### R3实际探测

实际运行 `getent hosts github.com`（退出2）、curl HEAD到github.com与go.dev（均退出6）、`go version`（退出0，go1.23.2）。PATH查询未找到Yunka/protoc。没有重复执行无前提的消费者命令；原始输出在本轮外部evidence目录。

GitHub连接器读取治理仓库元数据成功；main ref查询409(empty)。这些是连接器GET结果，既不是本地git命令，也不是上传/集成成功。现有IoT源码导出run33969592332的制品9970509351已过期且消费者SHA不匹配；没有下载、重新运行工作流或篡改消费者CI。

下列固定源码与工具变量仍为待提供输入。R3未设置虚构路径，未将恢复G的bundle当作F/B/I源码。

## 2. 源码与工具变量

恢复执行时为真实云端路径赋值，而不是复用本轮失败 clone 后的空目录：

```bash
F_REF=<固定到 cc546c7d25ee316442eafacd3c93599f7691f22a 的框架参考checkout>
B=<固定到 fed9051bae618ab28df200038682f808c82e854b 的Biz根>
B_YUNKA=<固定到 33b98ceba57494abda2299e4f0290a5651dab4bc 的Biz相邻yunka.io根>
I=<固定到 bcd20632b666405c3f7a8fe8d53f591c78450087 的IoT根>
GO125=<既有、已核验的Go1.25.13可执行文件>
EVIDENCE=<消费者审计根以外的本轮产物目录>
```

这是变量说明，不是已执行的 shell 脚本。不要用框架参考 `F_REF` 代替 `B_YUNKA` 或 IoT 的子模块。所有涉及 `go run` 的命令还需已备妥依赖、隔离缓存与网络政策；禁止用环境自动下载 latest 偷换锁定工具。

## 3. CG-00 必需补齐的真实 Git 核验

状态：**BLOCKED / SOURCE_NOT_MATERIALIZED**。

在每个实际源码仓库运行并保存结果。下面标记内的代码是本轮文档执行回归直接提取的唯一 Git 身份块：

<!-- CG00:GIT-IDENTITY:BEGIN -->
```bash
git -C "$R" rev-parse --verify HEAD
git -C "$R" rev-parse --verify 'HEAD^{tree}'
git -C "$R" status --porcelain=v1 --untracked-files=all
```
<!-- CG00:GIT-IDENTITY:END -->

必须同时检查退出码、完整输出与预期值：HEAD 输出须等于该角色固定 commit，tree 输出须等于固定 tree，且分别以 `git cat-file -t` 确认 commit/tree 类型，status 输出为空。stdout 非空不代表成功；缺项、空输出、错对象和脏工作树均不能放行。

核验对象分别是 F_REF、B、B_YUNKA、I、I/third_party/yunka；HEAD 和 tree 必须与 BASELINES.json 中该角色的值一致。治理 G 的 Git 命令结果另存，不能拿 G 的 clean worktree 替代消费者。

额外执行：

```bash
git -C "$I" ls-tree HEAD -- third_party/yunka
git -C "$I/third_party/yunka" rev-parse HEAD
git -C "$I/third_party/yunka" status --porcelain=v1 --untracked-files=all
```

子模块条目、物化 HEAD、工作树清洁性是三个不同证据；本轮只取得远端 gitlink 与 commit 对象。

## 4. Biz 的既有命令

### 4.1 原生入口（来源已核对，尚未运行）

| 命令 | 工作目录／来源 | 前置条件 | CG-00 可否执行 |
|---|---|---|---|
| make yunka-source-check | B；[B_MAKE][B_SOURCE_CHECK] | B_YUNKA 精确SHA、普通.git目录、Go、模块依赖 | 可，只读核验；当前 BLOCKED |
| make workspace-check | B；[B_MAKE] | 现有 consumer-resolution-check.sh 及其环境 | 命令存在；脚本本轮未展开，需先读再运行 |
| make check | B；[B_MAKE] | Go、protoc、框架app、商业目录检查输入 | 记录到目录；本轮未运行 |
| make test | B；[B_MAKE] | 当前模块依赖 | 记录到目录；本轮未运行 |
| make verify | B；[B_MAKE] | workspace/check/test/vet/build 全部条件 | 后续资格，不代替CG-00 context |
| make consumer-certify | B；[B_MAKE] | 锁源检查、workspace与GOWORK=off双模式 | 后续资格；本轮未运行 |
| make pressure | B；[B_MAKE] | 前项通过及YUNKA_TEST_MYSQL_DSN | 行为资格；本轮不启动数据库 |
| make init / make generate | B；[B_MAKE] | 会写项目／generated／依赖 | **DEFERRED_WRITES，不在CG-00运行** |

`make generate` 明确先运行框架生成器，再 `go mod tidy`，再 `commercial-generate`；不能标为只读。`make check`还调用商业目录检查，不能省略后者后声称完整 check。[B_MAKE]

### 4.2 消费者 context/help（由源构建入口组合，待真实确认）

以下是依据 Makefile 的 `cd .../app && go run ./cmd` 模式组合出的待核验调用，不是本轮已成功执行，也不是从实际 `--help` 得到的兼容承诺：

```bash
(cd "$B_YUNKA/app" && "$GO125" run ./cmd --help)
(cd "$B_YUNKA/app" && "$GO125" run ./cmd context --help)
(cd "$B_YUNKA/app" && "$GO125" run ./cmd context --root "$B" --json)
```

状态：`DERIVED_INVOCATION_NOT_RUN`。先核对锁定版本的真实 help；不支持则记录能力缺口，不改 pin。不假定该旧版本具有框架 main 的所有 AG 命令。

### 4.3 模块解析

[B_SOURCE_CHECK] 已规定 `GOWORK=off go list -m` 检查模块身份、版本、replace Dir 与物理目标目录。建议记录完整 JSON 作为本轮恢复核验的附加输出：

```bash
(cd "$B" && GOWORK=off "$GO125" list -mod=readonly -m -json yunka.io/framework yunka.io/gateway yunka.io/pkg)
```

该 JSON 输出调用是本轮拟定的采集形式，不是已有脚本原命令，也尚未执行。所有输出须核对 Replace.Dir、源码SHA、依赖文件前后hash，不能只读 Version 字段。

## 5. IoT 的既有命令

### 5.1 原生 Make 目标

来源：[I_MAKE]。Makefile 明确要求 Go 1.25.13、protoc 3.21.12、protoc-gen-go v1.36.11、protoc-gen-go-grpc 1.6.2。

| 命令 | 效果与前置条件 | 本轮状态 |
|---|---|---|
| make -C "$I/backend-yunka" yunka-revision-check | gitlink、物化子模块HEAD、clean、DSL include存在 | SOURCE_CONFIRMED_NOT_RUN |
| make -C "$I/backend-yunka" yunka-cli-check GO="$GO125" | 上项＋Go精确版本 | SOURCE_CONFIRMED_NOT_RUN |
| make -C "$I/backend-yunka" yunka-context GO="$GO125" | 上项＋从子模块app运行context --root仓库根 --json | **CG-00必需，BLOCKED** |
| make -C "$I/backend-yunka" yunka-toolchain-check GO="$GO125" TOOLS_DIR="$TOOLS" | Go和三项protobuf工具精确版本 | SOURCE_CONFIRMED_NOT_RUN |
| make -C "$I/backend-yunka" yunka-check GO="$GO125" TOOLS_DIR="$TOOLS" | 固定--full、protoc、DSL include检查 | 本轮不宣称执行或通过 |
| make -C "$I/backend-yunka" yunka-generate ... | 写生成产物 | **DEFERRED_WRITES** |

`TOOLS` 必须是云端审计根外的已准入工具目录。Makefile 默认 `.tools` 位置是源码已有事实，不表示本轮授权向消费者内安装工具。

### 5.2 help 与不同 module/profile

按 [I_MAKE] 的固定 CLI 构造可执行以下待核验 help：

```bash
GOWORK="$I/third_party/yunka/go.work" "$GO125" -C "$I/third_party/yunka/app" run ./cmd --help
GOWORK="$I/third_party/yunka/go.work" "$GO125" -C "$I/third_party/yunka/app" run ./cmd context --help
```

状态：`DERIVED_INVOCATION_NOT_RUN`。context 本身优先使用原生 `yunka-context` 目标。

以下正常 Go 检查来自 [I_CLOUD]；它们在 backend-yunka 中以 `GOWORK=off` 执行，是后续任务命令，不代表CG-00已跑：

```bash
go mod tidy -diff
go test -mod=readonly -count=1 -timeout=10m ./...
go vet -mod=readonly ./...
go test -mod=readonly -race -count=1 -timeout=10m ./...
```

根 go.work 包含 `backend` 和 `backend-yunka`；上列命令不覆盖 legacy backend，更不覆盖 web。不要用根目录一次 `go test ./...` 代替完整多模块清点。

## 6. 已有云端入口（本轮只读，不触发）

| 项目 | 路径 | 已读定义 | 本轮执行 |
|---|---|---|---|
| Biz | .github/workflows/ce08-qualification.yml | 固定Yunka SHA，读取tools/toolchain.env，MySQL8.4，运行scripts/ce08_qualify.sh | 无新run ID |
| IoT | .github/workflows/yu30-regression.yml | 固定子模块、Go1.25.13、4个suite、正常后端及浏览器检查 | 无新run ID |

工作流具有 `workflow_dispatch` 定义，不意味着本对话有可调用的 dispatch 权限或执行接口。本轮发现查询未提供可用云端执行动作；没有通过变更消费者CI来制造入口。

[I_REGRESSION] 的整套脚本会进行两轮生成、调用治理压力脚本、前端安装与E2E；它不是CG-00只读采集命令。Biz `ce08_qualify.sh` 的脚本内部本轮未展开，不对其无副作用作保证。

## 7. 尚未建立的命令能力

未创建任何 `make cg-*` 目标。CG-04A/04B 的计划和对账程序尚未实现。没有安装全局 `yunka` 二进制，也没有一个“当前main版审计CLI”被自动选为消费者工具。

待恢复后先从**各自固定消费者版本**的真实 help 中记录 ownership、audit、change 的可用子命令及参数；若不存在，不把框架main文档当作支持证据。

## 8. CG-00 文档执行回归与完成条件

在 G 执行 JSON 解析、版本/路径/来源交叉检查、docs-only diff、Git 完整性、patch/bundle 重放。另从第3节标记代码块提取真实 argv，执行 HEAD/tree/status 并比对 Git 对象；回归必须拒绝旧错误表达式、将 tree 换成 commit、删命令、添加无关命令以及脏工作树。用带空格的目录验证 R 的路径处理。该检查脚本仅是本轮外部证据工具，不是 CG-01 配方，也没有新增 make cg-* 产品入口。

R2 继续固定原 CG-00 consumer SHA：本轮观察到 Biz main 为 `12f33db5632a81c04b9de7634b5d37baf5a2bb1a`，但实际待验收 Biz 仍为 `fed9051bae618ab28df200038682f808c82e854b`。不得自动 checkout main 来偷换输入。

消费者级Git元数据、固定版本context/help、模块解析身份、远端同步仍未完成。只有补齐这些结果后才更新CG-00状态；不能把文档通过或历史工作流成功升级为本轮DONE。


## R3远端交付前置对账（只读，不是发布命令）

| 步骤 | 需要证据 | 当前值／处置 |
|---|---|---|
| 仓库身份 | repo id、full_name | 1364383040 / hvritual/engineering-governance；已核实 |
| 发布可见性 | approved visibility 对比 observed visibility | private 对 public；停止上传 |
| 写入能力 | 元数据和实际授权分开 | push=true已报告，未做写入测试 |
| 分支现状 | refs/heads/main 的实际响应 | 409空仓库；不是已有main提交 |
| 历史保全 | 本地R2父提交、完整对象、tree | 本轮已从原bundle恢复，不能新建假历史替代 |
| 实际执行 | 固定消费者命令日志、有效工具身份 | 未运行，保持BLOCKED |
| 最终回读 | 获准交付后准确commit/tree与内容 | 尚无，不能生成成功回执 |

仅当私有要求满足，或用户明确修改公开边界时，才可进入上传；repo可读/可写、README存在、分支名main都不代替授权和回读。这里没有修改Yunka CLI、消费者Makefile或GitHub workflow。

## 离线源码恢复输入清单（尚未交付）

需要可核验的完整Git对象分别覆盖F_REF、B、B_YUNKA、I、I/third_party/yunka，确切SHA以BASELINES.json为准；一个Yunka bundle可包含多个所需commit，但不得通过切换同一个源码位置混淆消费者。工具包应包含锁定Go及依赖可用条件，不能包含访问令牌或生产凭据。只有Go二进制而没有相应依赖，也不足以运行消费者CLI。

本轮的go.dev下载索引尝试没有取得文件；搜索无结果不证明Go版本不存在。所有可用性判断基于实际输入，不以历史工作流成功补齐当前空值。

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
