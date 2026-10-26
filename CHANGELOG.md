# Changelog

## [0.20.0](https://github.com/KRoperUK/pysolarcloud/compare/v0.19.0...v0.20.0) (2026-10-26)


### Features

* **control:** fall back to per-setting reads when bulk read is rejected ([0973084](https://github.com/KRoperUK/pysolarcloud/commit/097308420b15bcdc7772bd8512a2418c2e733564)), closes [#129](https://github.com/KRoperUK/pysolarcloud/issues/129)
* **plants:** add DeviceFaultStatus alias ([592f14c](https://github.com/KRoperUK/pysolarcloud/commit/592f14c8fb92db298165c3566984a7622fb0f6ff)), closes [#132](https://github.com/KRoperUK/pysolarcloud/issues/132)


### Bug Fixes

* **auth:** accept Server enum for Auth host ([4fb778d](https://github.com/KRoperUK/pysolarcloud/commit/4fb778dc2d890256ff9efb4b1fa404e4848c6992)), closes [#123](https://github.com/KRoperUK/pysolarcloud/issues/123)
* **auth:** login and token-reply hardening (P2) ([9160c9c](https://github.com/KRoperUK/pysolarcloud/commit/9160c9c578803044a0062d12351fef5de015d006))
* **auth:** validate token reply on first authorization ([ec15175](https://github.com/KRoperUK/pysolarcloud/commit/ec1517580ec0305eca770d9a9ee243d72973b713)), closes [#119](https://github.com/KRoperUK/pysolarcloud/issues/119)
* **control:** coerce quoted task status when polling ([8751de3](https://github.com/KRoperUK/pysolarcloud/commit/8751de3e8d30e928cc5bfea58d8df083351f377c)), closes [#121](https://github.com/KRoperUK/pysolarcloud/issues/121)
* **control:** quoted task status and shared reply logic (P4) ([b1e8045](https://github.com/KRoperUK/pysolarcloud/commit/b1e8045622fcf7c78819717eb8e9d6792c997cbd))
* history interval, point names, read-all fallback (P3) ([a070589](https://github.com/KRoperUK/pysolarcloud/commit/a070589d063464d325dd368a251f1654a282e3a0))
* **plants:** convert history interval with total seconds ([ccc54b7](https://github.com/KRoperUK/pysolarcloud/commit/ccc54b708f4f967de99155e887c9183a5f273c97)), closes [#120](https://github.com/KRoperUK/pysolarcloud/issues/120)
* **plants:** raise ValueError for unknown measure point names ([a05df06](https://github.com/KRoperUK/pysolarcloud/commit/a05df068ca7e5feeccbdc85bec6255131feb3f11)), closes [#122](https://github.com/KRoperUK/pysolarcloud/issues/122)
* **plants:** tag list-or-map wrappers as dict | list ([5dc6229](https://github.com/KRoperUK/pysolarcloud/commit/5dc6229d69f674fbf82a4ad1076b587d44cb6528)), closes [#126](https://github.com/KRoperUK/pysolarcloud/issues/126)
* **plants:** tolerate devices missing type or fault status ([d23580e](https://github.com/KRoperUK/pysolarcloud/commit/d23580eb9c0aef7949f9160a392d8969689b977c)), closes [#130](https://github.com/KRoperUK/pysolarcloud/issues/130)
* **user-auth:** drop unreachable success code from re-login codes ([472578c](https://github.com/KRoperUK/pysolarcloud/commit/472578c99356adab64e5828c87818e01ad8997d5)), closes [#128](https://github.com/KRoperUK/pysolarcloud/issues/128)
* **user-auth:** fail login when the reply has no user_id ([321f774](https://github.com/KRoperUK/pysolarcloud/commit/321f7740e270b6c2d11de31f9b8458f2800dd31c)), closes [#133](https://github.com/KRoperUK/pysolarcloud/issues/133)
* **user-auth:** wrap undecodable replies in typed exception ([004ac81](https://github.com/KRoperUK/pysolarcloud/commit/004ac81fdae4c7416f6a8a007660353d044047da)), closes [#127](https://github.com/KRoperUK/pysolarcloud/issues/127)


### Performance Improvements

* **user-auth:** parse the login key once per session ([41d5148](https://github.com/KRoperUK/pysolarcloud/commit/41d51486a97bc9014d37f19718187ae176fe320f)), closes [#131](https://github.com/KRoperUK/pysolarcloud/issues/131)


### Documentation

* configure agent skills (issue tracker, triage labels, domain docs) ([e3ad3e7](https://github.com/KRoperUK/pysolarcloud/commit/e3ad3e7f696d9952003904532e16f4dc1b5466f9))
* configure agent skills (issue tracker, triage labels, domain docs) ([8b1cba8](https://github.com/KRoperUK/pysolarcloud/commit/8b1cba8d4de95643c34d1a105fd839971f518051))

## [0.19.0](https://github.com/KRoperUK/pysolarcloud/compare/v0.18.0...v0.19.0) (2026-10-09)


### Features

* app-native param-set flow — gate, template discovery, issue, read-back, cancel ([#92](https://github.com/KRoperUK/pysolarcloud/issues/92)) ([ead4b65](https://github.com/KRoperUK/pysolarcloud/commit/ead4b65726699f1b5f22290aafad48fbf6b6df9a))
* app-native param-set flow — gate, template discovery, issue, read-back, cancel ([#92](https://github.com/KRoperUK/pysolarcloud/issues/92)) ([5b35424](https://github.com/KRoperUK/pysolarcloud/commit/5b35424f46a3d054fa94ae9dede02109b801e78a))
* ship the measure-point catalog as package data ([09f90b2](https://github.com/KRoperUK/pysolarcloud/commit/09f90b2a652447f65f4c79fe01dae214b6a7ef23))
* ship the measure-point catalog as package data ([d1fb404](https://github.com/KRoperUK/pysolarcloud/commit/d1fb404b31897965af4a239661bee7fdf731e497))

## [0.18.0](https://github.com/KRoperUK/pysolarcloud/compare/v0.17.0...v0.18.0) (2026-09-15)


### ⚠ BREAKING CHANGES

* `async_get_device_realtime` now raises `DeviceEndpointUnavailable` instead of returning `{}` when the per-device endpoint is unavailable and no `user_auth` fallback is supplied. Callers that want the old behaviour can catch the exception, or call `async_device_realtime_supported` first.

### Features

* app-native scheduling & home operation-mode endpoints in UserAuth ([#95](https://github.com/KRoperUK/pysolarcloud/issues/95)) ([#105](https://github.com/KRoperUK/pysolarcloud/issues/105)) ([581d3d6](https://github.com/KRoperUK/pysolarcloud/commit/581d3d6f0c8712aa710182712e5134047841875e))
* raise DeviceEndpointUnavailable when the per-device endpoint is absent ([#86](https://github.com/KRoperUK/pysolarcloud/issues/86)) ([#103](https://github.com/KRoperUK/pysolarcloud/issues/103)) ([6f0c427](https://github.com/KRoperUK/pysolarcloud/commit/6f0c427443ff73836909d82c87290d0043ef502e))

## [0.17.0](https://github.com/KRoperUK/pysolarcloud/compare/v0.16.0...v0.17.0) (2026-09-15)


### Features

* fall back to the user-account per-device realtime call when the OpenAPI endpoint returns nothing ([#85](https://github.com/KRoperUK/pysolarcloud/issues/85)) ([#101](https://github.com/KRoperUK/pysolarcloud/issues/101)) ([cfc7f78](https://github.com/KRoperUK/pysolarcloud/commit/cfc7f7896605c62c949b8d552d35a2533d7d3646))

## [0.16.0](https://github.com/KRoperUK/pysolarcloud/compare/v0.15.2...v0.16.0) (2026-09-15)


### ⚠ BREAKING CHANGES

* UserAuth.async_get_device_realtime now takes a device ps_key (from async_get_devices) as its single positional argument instead of (ps_id, device_sn), and returns data keyed by device uuid ({uuid: {point_id: {"id", "value", "unit", "name"}}}) instead of a flat {point_id: {"value", "unit"}} map. It now posts to queryDeviceRealTimeDataByPsKeys; the previous /v1/devService/queryDevice path did not exist in the app. Migrate by passing the device ps_key from async_get_devices and reading each device's points under its uuid.

### Features

* iSolarCloud app-parity user-account API (endpoint fixes + EV charger, battery, fault, energy & history reads) ([#99](https://github.com/KRoperUK/pysolarcloud/issues/99)) ([30d06b8](https://github.com/KRoperUK/pysolarcloud/commit/30d06b890d27f63c11eed9f26876d6ab8965a4d5))

## [0.15.2](https://github.com/KRoperUK/pysolarcloud/compare/v0.15.1...v0.15.2) (2026-09-15)


### Bug Fixes

* let callers override encode_parameter bounds for per-device limits ([#87](https://github.com/KRoperUK/pysolarcloud/issues/87)) ([b8c277c](https://github.com/KRoperUK/pysolarcloud/commit/b8c277cc17f070b226aad67822abd97e2a0a4d17))


### Documentation

* document the contribution workflow and the rules main enforces ([#83](https://github.com/KRoperUK/pysolarcloud/issues/83)) ([134d061](https://github.com/KRoperUK/pysolarcloud/commit/134d061dbc593b3de0ae26f63217d324a72067ed))

## [0.15.1](https://github.com/KRoperUK/pysolarcloud/compare/v0.15.0...v0.15.1) (2026-09-12)


### Bug Fixes

* **auth:** map the India region to web3.isolarcloud.in with cloudId=9 ([#80](https://github.com/KRoperUK/pysolarcloud/issues/80)) ([0a1c440](https://github.com/KRoperUK/pysolarcloud/commit/0a1c440565c2b8009ff3113b948ff2508dbf54a7))

## [0.15.0](https://github.com/KRoperUK/pysolarcloud/compare/v0.14.0...v0.15.0) (2026-07-19)


### Features

* **control:** typed ParameterSpec model + Control.PARAMETERS map ([#75](https://github.com/KRoperUK/pysolarcloud/issues/75)) ([e2f6216](https://github.com/KRoperUK/pysolarcloud/commit/e2f6216304a46cab079a6389174c3d7f5cdb1968))
* library follow-ups: retry_after, partial refresh, DeviceNotWritable, lazy crypto, console URL ([d3b8c2a](https://github.com/KRoperUK/pysolarcloud/commit/d3b8c2ae2e04a2d3b933b02754362772fde5f47b))
* **plants:** async_iter_historical_data — transparent pagination helper ([#74](https://github.com/KRoperUK/pysolarcloud/issues/74)) ([b06ffe1](https://github.com/KRoperUK/pysolarcloud/commit/b06ffe1ee7cdee5ae35c2c80a1caf3afb95d355e))
* **server:** expose per-region web console URL from the Server enum ([6a122f2](https://github.com/KRoperUK/pysolarcloud/commit/6a122f2ed29b22924ba9459842b677e20c79b6b5))


### Bug Fixes

* **auth:** expose server retry_after hint on RateLimitError ([88f9800](https://github.com/KRoperUK/pysolarcloud/commit/88f9800b09db26c482ebe63f7c2c9fa38d335420))
* **auth:** preserve refresh_token when partial refresh response omits it ([fab3a1e](https://github.com/KRoperUK/pysolarcloud/commit/fab3a1ea4fdee5d2e04f870ff0d02c994821bfa3))
* **control:** typed DeviceNotWritableError + centralise envelope routing ([952d933](https://github.com/KRoperUK/pysolarcloud/commit/952d933fd8ad3b20edfa5c4ee87d4ba218bafe7d))


### Documentation

* add shields.io badges to README ([#60](https://github.com/KRoperUK/pysolarcloud/issues/60)) ([fbac60a](https://github.com/KRoperUK/pysolarcloud/commit/fbac60ada7a89a39e90ce9c1965bda0e343e4c4a))

## [0.14.0](https://github.com/KRoperUK/pysolarcloud/compare/v0.13.0...v0.14.0) (2026-07-18)


### Features

* spike user-account control endpoint probe ([#271](https://github.com/KRoperUK/pysolarcloud/issues/271)) ([#58](https://github.com/KRoperUK/pysolarcloud/issues/58)) ([de861a8](https://github.com/KRoperUK/pysolarcloud/commit/de861a8776982a418c8ea58e44c9fb5f8ff193c8))

## [0.13.0](https://github.com/KRoperUK/pysolarcloud/compare/v0.12.1...v0.13.0) (2026-07-18)


### Features

* add device list, per-device realtime, and historical data to UserAuth ([#53](https://github.com/KRoperUK/pysolarcloud/issues/53)) ([#56](https://github.com/KRoperUK/pysolarcloud/issues/56)) ([63497d7](https://github.com/KRoperUK/pysolarcloud/commit/63497d7b5cefb109846a83b476a1dc551074ba88))

## [0.12.1](https://github.com/KRoperUK/pysolarcloud/compare/v0.12.0...v0.12.1) (2026-07-17)


### Bug Fixes

* historical data loop bug, auth_url default, authorize raises, mypy target, typo ([#54](https://github.com/KRoperUK/pysolarcloud/issues/54)) ([bd25eb2](https://github.com/KRoperUK/pysolarcloud/commit/bd25eb2dc9de0e81fdfebf5e5617a39c23be7cb7))

## [0.12.0](https://github.com/KRoperUK/pysolarcloud/compare/v0.11.0...v0.12.0) (2026-07-16)


### Features

* UserAuth.async_get_plant_detail (getPsDetail) for user-account realtime ([#269](https://github.com/KRoperUK/pysolarcloud/issues/269)) ([#46](https://github.com/KRoperUK/pysolarcloud/issues/46)) ([2d102ca](https://github.com/KRoperUK/pysolarcloud/commit/2d102ca9c4a702a93be9362cbe81b44eab7bdc29))

## [0.11.0](https://github.com/KRoperUK/pysolarcloud/compare/v0.10.4...v0.11.0) (2026-07-16)


### Features

* UserAuth clean-room user-account (app/web) login ([#40](https://github.com/KRoperUK/pysolarcloud/issues/40)) ([#42](https://github.com/KRoperUK/pysolarcloud/issues/42)) ([146b5ae](https://github.com/KRoperUK/pysolarcloud/commit/146b5aee8b31922a4fc4535a3f654813eed9580a))


### Bug Fixes

* surface login_state failures as typed AuthError with attempts left ([#40](https://github.com/KRoperUK/pysolarcloud/issues/40)) ([#45](https://github.com/KRoperUK/pysolarcloud/issues/45)) ([23183c4](https://github.com/KRoperUK/pysolarcloud/commit/23183c49228a8162552c637d821df0189922d8da))

## [0.10.4](https://github.com/KRoperUK/pysolarcloud/compare/v0.10.3...v0.10.4) (2026-07-13)


### Bug Fixes

* re-enable energy management mode (10003) for dispatch ([#36](https://github.com/KRoperUK/pysolarcloud/issues/36)) ([58adfe1](https://github.com/KRoperUK/pysolarcloud/commit/58adfe11f70c8a1944cc3fc3a5b41251c64114fa))

## [0.10.3](https://github.com/KRoperUK/pysolarcloud/compare/v0.10.2...v0.10.3) (2026-07-06)


### Bug Fixes

* chunk point_id_list so realtime requests never exceed the 100-point cap ([5a115cb](https://github.com/KRoperUK/pysolarcloud/commit/5a115cb9a29a5593235713f18f27bd0021fa723f))
* chunk point_id_list so realtime requests never exceed the 100-point cap ([5a115cb](https://github.com/KRoperUK/pysolarcloud/commit/5a115cb9a29a5593235713f18f27bd0021fa723f))

## [0.10.2](https://github.com/KRoperUK/pysolarcloud/compare/v0.10.1...v0.10.2) (2026-07-06)


### Bug Fixes

* don't pad device realtime with plant points (100-point cap) ([#32](https://github.com/KRoperUK/pysolarcloud/issues/32)) ([f00c5b3](https://github.com/KRoperUK/pysolarcloud/commit/f00c5b3ce971c7aac01642f9a591c3a96f605ffa))

## [0.10.1](https://github.com/KRoperUK/pysolarcloud/compare/v0.10.0...v0.10.1) (2026-07-06)


### Bug Fixes

* unwrap nested device_point in getDeviceRealTimeData responses ([#30](https://github.com/KRoperUK/pysolarcloud/issues/30)) ([f61402b](https://github.com/KRoperUK/pysolarcloud/commit/f61402b681a1a354c94bd85c46710f607cec4681))

## [0.10.0](https://github.com/KRoperUK/pysolarcloud/compare/v0.9.1...v0.10.0) (2026-07-06)


### Features

* add PARAMETER_SPECS for reactive-power / power-factor control ([#28](https://github.com/KRoperUK/pysolarcloud/issues/28)) ([371d5e5](https://github.com/KRoperUK/pysolarcloud/commit/371d5e51a27fd6540a46642e55b22f4a35a60cd3))

## [0.9.1](https://github.com/KRoperUK/pysolarcloud/compare/v0.9.0...v0.9.1) (2026-07-05)


### Bug Fixes

* send ps_key_list to getDeviceRealTimeData (fixes result_code 009) ([#26](https://github.com/KRoperUK/pysolarcloud/issues/26)) ([d784012](https://github.com/KRoperUK/pysolarcloud/commit/d78401260d672e705a2ddab3691b564a6a98758d))

## [0.9.0](https://github.com/KRoperUK/pysolarcloud/compare/v0.8.0...v0.9.0) (2026-07-03)


### Features

* raise typed AuthError/RateLimitError from API errors ([c9e6d31](https://github.com/KRoperUK/pysolarcloud/commit/c9e6d31c5382495b3d79b7d5200a0c0517d0b8fe))
* raise typed AuthError/RateLimitError from API errors ([583ab13](https://github.com/KRoperUK/pysolarcloud/commit/583ab135219b8a3d91d3ba14ebf127c2e37ec3f6)), closes [#23](https://github.com/KRoperUK/pysolarcloud/issues/23)

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
