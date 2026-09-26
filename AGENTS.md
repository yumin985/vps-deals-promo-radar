ILANG
[TYPE:project_rules][PROJECT:vps-deals][LANG:zh]

::STATE{@PROJECT, purpose:公开 VPS 优惠信息静态站, config:.ilang/site.ilang}

::MODULE{ALLOWED}
  读取 .ilang/site.ilang 中的厂商、官方入口、字段和站点规则。
  只请求公开的官方 sitemap、feed 或优惠/价格页面；尊重 robots.txt、超时和站点限制。
  用纯 Python 标准库抓取和构建静态文件；GitHub Actions 定时更新 data/offers.json 和 site/。
  记录来源链接、抓取时间和厂商实际提供的价格/有效期；缺少字段就省略。
::END

::MODULE{FORBIDDEN}
  不得编造优惠、价格、佣金、有效期、流量、排名或收益。
  不得绕过登录、验证码、反爬、访问控制或 robots.txt；不得抓取登录后内容。
  不得伪造联盟关系或隐藏商业链接；affiliate_url 为空时必须使用官方裸链。
  不得把密钥、令牌、账户资料写入仓库、日志、站点文件或页面。
  不得声称站点已上线、工作流已运行或页面已通过富媒体测试，除非实际验证。
::END

::RULE{site.ilang_is_the_only_provider_and_site_config_source}
::RULE{unknown_or_unparseable_source_data⇒omit_and_report}
