# CG-00 路径与所有权冻结

状态：**COMPLETE / R6**。

## 1. 治理主线写入范围

`hvritual/engineering-governance` main 的 CG-00 状态写入仍限定为：

```text
docs/CG-CHARTER.md
docs/BASELINES.json
docs/PATH-OWNERSHIP.md
docs/COMMAND-CATALOG.md
```

R6 为补齐真实执行证据，在治理仓库控制分支 `control/cg00-r6-exec` 增加一次性资格 workflow。该文件不合入治理 main，不进入 Biz/IoT/Yunka，也不扩展 Yunka 脚手架。

## 2. 消费者与框架权限

| 对象 | CG-00 权限 |
|---|---|
| Yunka 框架参考及两套消费者锁定源码 | 只读；不升级 CLI/生成器/Runtime |
| Biz 业务、配置、依赖、generated | 只读 |
| IoT backend、backend-yunka、web、third_party | 只读 |
| IoT `server/**` | 永久禁止写入 |
| 消费者 CI/权限/保护规则 | 不修改 |
| 治理控制分支资格 harness | 仅用于实际命令执行与证据上传，不成为产品运行时 |

## 3. R6 写入证明

资格运行在 `GOFLAGS=-mod=readonly` 下执行。依赖文件前后 SHA-256 清单一致；Biz、Biz Yunka、IoT、IoT Yunka 的最终 `git status --porcelain --untracked-files=all` 均为空。

CG-01 开始后，新的规则、fixture、recipe 或工具锁必须由 CG-01 自己声明写入范围；CG-00 的四文档 allowlist 不自动授权后续治理资产。
