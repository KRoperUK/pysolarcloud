# Changelog

## [0.8.0](https://github.com/KRoperUK/pysolarcloud/compare/v0.7.0...v0.8.0) (2026-07-03)


### Features

* add getDevPropertyPointValue and getOpenPointInfo wrappers ([#16](https://github.com/KRoperUK/pysolarcloud/issues/16)) ([e359b1d](https://github.com/KRoperUK/pysolarcloud/commit/e359b1d459505da7d0604275d8a8f204df98fac1))
* add missing DeviceType members (charger, optimizer, microinverter, …) ([#17](https://github.com/KRoperUK/pysolarcloud/issues/17)) ([e2869a6](https://github.com/KRoperUK/pysolarcloud/commit/e2869a6b1e54dbb2800663c5daacef266e3486c8))


### Bug Fixes

* add plant point 83743 and correct 83237 unit comment ([#18](https://github.com/KRoperUK/pysolarcloud/issues/18)) ([d6c4faa](https://github.com/KRoperUK/pysolarcloud/commit/d6c4faa55e8e6e320558aabbee146e1eeba7ad59))
* audit remediation — error contract, refresh lock, control safety, coverage ([#9](https://github.com/KRoperUK/pysolarcloud/issues/9)–[#19](https://github.com/KRoperUK/pysolarcloud/issues/19)) ([bed7f8c](https://github.com/KRoperUK/pysolarcloud/commit/bed7f8c7d9ea94f39b02d540ec249177422c9608))
* bound wait_for_task with a deadline ([#12](https://github.com/KRoperUK/pysolarcloud/issues/12)) ([be6459d](https://github.com/KRoperUK/pysolarcloud/commit/be6459d8be35ca3dabbf044d4e433cc7eb152b74))
* close owned session, add timeout, and raise_for_status uniformly ([#14](https://github.com/KRoperUK/pysolarcloud/issues/14)) ([b5507d2](https://github.com/KRoperUK/pysolarcloud/commit/b5507d2912e7b48bf9576eea3c711c84fe156605))
* correct token-endpoint return types and honor Control lang ([#15](https://github.com/KRoperUK/pysolarcloud/issues/15)) ([1d99280](https://github.com/KRoperUK/pysolarcloud/commit/1d9928020b89b3536ae9b1a1ff1b216a91d62265))
* detect API errors via result_code, not an "error" key ([#9](https://github.com/KRoperUK/pysolarcloud/issues/9)) ([102539f](https://github.com/KRoperUK/pysolarcloud/commit/102539f5d4395759f275cf10869ebfd160a39bc2))
* enforce parameter min/max bounds in encode_parameter ([#13](https://github.com/KRoperUK/pysolarcloud/issues/13)) ([7b9b3b4](https://github.com/KRoperUK/pysolarcloud/commit/7b9b3b472a4026d4c02dce323ee4f63cc7a0b7be))
* pass list plant_id through as ps_id_list in historical data ([#11](https://github.com/KRoperUK/pysolarcloud/issues/11)) ([5b0fe47](https://github.com/KRoperUK/pysolarcloud/commit/5b0fe474c72174eaedb5479d3515832dab190923))
* serialize token refresh with an asyncio.Lock ([#10](https://github.com/KRoperUK/pysolarcloud/issues/10)) ([a374d9d](https://github.com/KRoperUK/pysolarcloud/commit/a374d9d5cbb134fd8aebf21281dda90984cbbd74))

## [0.7.0](https://github.com/KRoperUK/pysolarcloud/compare/v0.6.0...v0.7.0) (2026-07-03)


### Features

* ship py.typed, fix type annotations, add control-parameter specs ([749012b](https://github.com/KRoperUK/pysolarcloud/commit/749012b1e389602c758a6421959298715650e3d6))
* ship py.typed, fix type annotations, add control-parameter specs ([fe7e04d](https://github.com/KRoperUK/pysolarcloud/commit/fe7e04dbbb44ec83a3c6bb8a28a7e91886a3470e))


### Bug Fixes

* **ci:** inline PyPI publish (trusted publishing rejects reusable workflows) ([dab57ea](https://github.com/KRoperUK/pysolarcloud/commit/dab57ea2bc558a2e764b0db46a2a55295343768a))
* **ci:** inline PyPI publish; reusable workflows unsupported by trusted publishing ([3f6d0fd](https://github.com/KRoperUK/pysolarcloud/commit/3f6d0fddcf2297817ada1a3e84dc42defd484b11))

## [0.6.0](https://github.com/KRoperUK/pysolarcloud/compare/v0.5.0...v0.6.0) (2026-07-03)


### Features

* raise typed TokenRefreshError on failed token refresh ([f44270c](https://github.com/KRoperUK/pysolarcloud/commit/f44270c85e8ba0e36e3bbf597ef0141588ba1ced))
* raise typed TokenRefreshError on failed token refresh ([5620261](https://github.com/KRoperUK/pysolarcloud/commit/5620261ad9429f626ceb3ddfb9f82b96b6982007))
