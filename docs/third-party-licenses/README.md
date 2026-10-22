# 第三方许可补充资料

本目录补充固定开发/验证依赖清单中未收集的准确版本许可正文，以及 certifi 再分发时的许可与源码定位资料。不是全部依赖、全部平台或法律审计；不替代三处导入代码的权利归属及 MIT 公开授权确认。

## 资料索引

机器索引见 [index.json](index.json)：记录准确版本、官方发行 tag 对应的固定 commit、源码归档 URL、PyPI wheel 文件名/URL/SHA-256、许可正文来源和 SHA-256。正文按上游原始字节保存，不改写、不替换版权署名。

| 依赖 | 本地资料 | 补充原因 |
| --- | --- | --- |
| dnspython 2.9.0 | [ISC LICENSE](dnspython-2.9.0/LICENSE) | 准确 wheel 无独立许可正文文件；保留官方 Contributors、Nominum、Google 声明 |
| fastmcp-slim 3.4.11 | [Apache-2.0 LICENSE](fastmcp-slim-3.4.11/LICENSE) | 准确 wheel 无独立许可正文文件；slim 子项目声明 Apache-2.0，沿用同源码树根 LICENSE |
| certifi 2026.10.19 | [上游 LICENSE](certifi-2026.10.19/LICENSE)、[MPL-2.0 正文](certifi-2026.10.19/MPL-2.0.txt) | 原许可已收集；补齐明确索引、条款正文与固定版本源码获取方式 |

FastMCP 固定 commit 的源码归档已检查大小写不敏感的 LICENSE/COPYING/NOTICE 文件名，仅找到根 LICENSE，未发现独立 NOTICE。这不是对所有源码头或第三方嵌入代码的穷尽审查。该版本升级后须重新检查，不能沿用旧结论。

## 再分发义务与适用边界

- **ISC**：保留适用版权及许可通知，不能只写许可名称；dnspython 源码已有许可头也不得删去。
- **Apache-2.0 第 4 条**：给接收者许可证副本；保留适用归属声明；修改上游文件时标明修改；上游如有适用 NOTICE 则按条款保留。根项目 MIT 不替代这些义务。
- **MPL-2.0 第 3 条**：保留受覆盖内容的声明和许可获取方式。若分发 Executable Form，须按条款提供对应 Source Code Form 的获取信息；修改的受覆盖源码仍受 MPL 约束，不因此自动要求整个 ai-device 采用 MPL。
- certifi 上游 LICENSE 将证书集合标为 MPL；准确版本源码可按 index.json 中固定 commit 的 repository/source_archive_url 获取。该定位不等于已经核实未来每个二进制交付物都满足源码提供义务；改动证书或其他受覆盖内容后须重新核对。

本次三包候选 wheel/sdist 不内嵌这三个依赖，其依赖安装由安装器另行处理。本目录不自动进入 wheel/sdist，不为仅声明依赖机械附带全部开发环境许可。

携带第三方 wheel 的离线 bundle/wheelhouse 则属于实际再分发：应按**实际包含的准确依赖集合**核对原有声明及补充正文，将适用资料和源码获取说明随交付物提供，并验证接收者在脱离仓库后仍能找到它们。本目录当前只准备资料，**没有实现自动随 bundle 交付，也不表示离线 bundle 已完成许可合规验收**。

## 维护和重新生成

- `docs/evidence/third-party-inventory.json` 及原有生成通知是历史环境快照，不回填伪造其安装文件记录。本目录独立承载补充资料。
- `scripts/capture_evidence.py` 的通知头固定链接到本目录；重新生成通知不覆盖本目录，也不宣称其中三个版本覆盖当前全部解析依赖。
- 更新版本时新增对应目录，重新获取并校验发行来源/制品/正文、NOTICE 和实际使用范围；不把旧版本正文自动标为新版本已审核。
- 许可资料中的 `upstream_modified_by_this_supplement=false` 仅表示本次保存的上游许可资料未被改写，不是对所有第三方源码未修改的全局证明。
- 校验：运行 `uv run --no-sync python -m pytest tests/test_license_materials.py -q`；测试检查本地哈希、索引、资料引用及生成通知入口。在线来源的一致性需另行核查，测试不联网。
