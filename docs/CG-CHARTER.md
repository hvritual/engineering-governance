# CG-00：外置治理执行章程

状态：**COMPLETE / R6 QUALIFIED；Issue #1 可关闭；CG-01 已获准启动。**

CG-00 的目标是冻结治理资产位置、五个源码角色、消费者 CLI/生成器/Runtime 来源、文件边界与实际命令前提。它不要求两个消费者拥有相同的 Yunka 能力，也不允许为了通过核验而升级消费者 pin。

## 1. 固定输入

| 角色 | Commit | Tree |
|---|---|---|
| 框架参考 | `cc546c7d25ee316442eafacd3c93599f7691f22a` | `84ef1130c3aead3fef83b990951790b6d996d2a0` |
| Biz | `fed9051bae618ab28df200038682f808c82e854b` | `5883f6783dbab1336fa25b36adb1f7e938086540` |
| Biz 相邻 Yunka | `33b98ceba57494abda2299e4f0290a5651dab4bc` | `fcc154390dc3a24ebb1819954fc883d0dd073c31` |
| IoT | `bcd20632b666405c3f7a8fe8d53f591c78450087` | `0c0d1a2da23de4b7315245faba2103ec7a64377e` |
| IoT 子模块 Yunka | `057ebcf88a87303eb633eb6e604d306f633dfac0` | `b2d82c574f6db1950b4323db839feda2d6fbc898` |

框架参考不是消费者升级目标。Biz 使用相邻 `../yunka.io` 锁定源，IoT 使用 `third_party/yunka` gitlink；二者不能被一个最新 CLI 替代。Biz 当前树完整但父提交历史为显式 shallow；需要祖先比较的后续任务必须重新取得完整历史。

## 2. R6 实际执行资格

R6 使用独立治理执行脚手架 `control/cg00-r6-exec`，没有修改 Biz、IoT 或 Yunka 工作流。GitHub-hosted Ubuntu 24.04 runner 固定 Go `1.25.13`，依赖通过 `https://proxy.golang.org,direct` 与 `sum.golang.org` 获取/校验，并在 `GOFLAGS=-mod=readonly` 下运行。依赖文件前后摘要一致，四个受审 worktree 均 clean。

资格 run：`34553907760`，job：`103122355997`，artifact：`10181806958`，artifact SHA-256：`329bd2785f5f7621d81bb80400a48b6567fb06cdb24e04bb352ef33e0cad5e47`。

六条指定 CLI 调用全部实际执行：

| 调用 | 结果 |
|---|---|
| Biz `--help` | PASS |
| Biz `context --help` | NOT_APPLICABLE：固定 CLI `33b98ceb…` 不存在 `context` 命令 |
| Biz `context --root … --json` | NOT_APPLICABLE：同上 |
| IoT `--help` | PASS |
| IoT `context --help` | PASS |
| IoT `context --root … --json` | PASS，schemaVersion 4 |

Biz 两个 `context` 调用的退出非零不是依赖问题：顶层 help 已成功，stderr 稳定为 `No help topic for 'context'`。CG-00 记录真实版本能力，不通过升级 Biz Yunka 来制造“六项全绿”。

Biz `scripts/consumer-resolution-check.sh` 实际通过，证明 Biz-local workspace 模块图与 `GOWORK=off` consumer graph 一致。

## 3. 边界

CG-00 没有修改 Yunka Runtime、生成器、业务代码、消费者 pin、generated、`server/**`、消费者 CI 或仓库保护。R6 的治理 workflow 仅是独立执行脚手架，保留在控制分支，不作为 Yunka 脚手架能力，也不进入消费者仓库。

IoT legacy `github.com/go-kit/kit` replace 指向缺失目录的既有问题继续标记 `INCOMPLETE`；CG-00 的完成不把该 legacy scope 冒充为 PASS。

## 4. Exit

CG-00 Exit 已满足：固定源码、Go、锁定依赖、六条 CLI 的真实能力边界、Biz 完整 workspace 和只读性均有实际证据。下一任务为 **CG-01：真实问题语料**。
