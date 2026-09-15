# sungrow-isolarcloud

[![PyPI version](https://img.shields.io/pypi/v/sungrow-isolarcloud)](https://pypi.org/project/sungrow-isolarcloud/)
[![Python versions](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE.txt)
[![CI](https://img.shields.io/github/actions/workflow/status/KRoperUK/pysolarcloud/test.yml?branch=main&label=CI)](https://github.com/KRoperUK/pysolarcloud/actions/workflows/test.yml)
[![Downloads](https://img.shields.io/pypi/dm/sungrow-isolarcloud)](https://pypi.org/project/sungrow-isolarcloud/)
[![GitHub Release](https://img.shields.io/github/v/release/KRoperUK/pysolarcloud)](https://github.com/KRoperUK/pysolarcloud/releases/latest)
[![Buy me a coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow)](https://buymeacoffee.com/kroperukc)

A maintained fork of the [pysolarcloud](https://github.com/bugjam/pysolarcloud) library for interacting with Sungrow's [iSolarCloud API](https://developer-api.isolarcloud.com/).

Install from PyPI:

```
pip install sungrow-isolarcloud
```

This fork adds:
* Support for requesting **additional / custom measure points** without modifying the upstream point map (useful for battery charge/discharge power fields that vary by inverter model).
* A best-effort **per-device realtime** helper for devices such as EV chargers (`Plants.async_get_device_realtime`).
* A **heartbeat** helper for External EMS dispatch mode (`Control.async_heartbeat` / `Control.heartbeat_loop`).
* Convenience constants for dispatch command value sets (`Control.CHARGE_DISCHARGE_COMMANDS`, `Control.FORCED_CHARGING`).

The package supports the following functionality:
* OAuth2 authentication
* Getting a list plants
* Getting details of a plant
* Getting devices of a plant
* Getting "real-time" data of a plant (Data is updated every 5 minutes according to Sungrow's documentation)
* Getting historical data
* Getting and updating grid control settings

## Quirks
The iSolarCloud API is quite new and not very mature. Some tips:
* The authorisation flow is based on OAuth2 but doesn't work exactly as you would expect
* The `state` parameter is not passed back after to the authorisation step. This makes it more tricky to resume the flow in a client application.
* User is asked to approve the authorisation if the flow is invoked again, e.g. in case the tokens have expired - unlike many OAuth2 implementations who will perform a "silent" authorisation if the user has already approved the access.
* The API documentation lists a lot of data points which do not seem to be returned from my inverter, it probably varies between models.
* There are different iSolarCloud servers for different regions, see the `pysolarcloud.Server` enum
* API endpoints accept a language code but respond with Chinese text when when English is requested

# Usage

## Register your app
1. Create an account in the [iSolarCloud Developer Portal](https://developer-api.isolarcloud.com/)
2. Create an app in the developer portal
   * Answer "Yes" to authorize with OAuth2.0
   * Enter a Redirect URL for your app (this can be changed later)
3. Wait for approval by Sungrow
4. Find the needed configuration details in the developer portal. You will need:
   * Appkey
   * Secret Key
   * Application Id (This is shown as a query parameter within the Authorize URL in the developer portal)

## Example

```python
from pysolarcloud import Auth, Server
from pysolarcloud.plants import Plants

app_key = "your app key"
secret_key = "your secret key"
app_id = "your app id"
redirect_uri = "your redirect uri"

auth = Auth(Server.Europe, app_key, secret_key, app_id)
url = auth.auth_url(redirect_uri)
```
1. Redirect user to `url`
2. User selects plant(s) and grants authorisation
3. iSolarCloud will redirect the user to `redirect_uri` with query parameter `code`
```python
await auth.async_authorize(code, redirect_uri)
plants_api = Plants(auth)
plant_list = await plants_api.async_get_plants()
if plant_list:
   print(f"{len(plant_list)} plants found:")
   for plant in plant_list:
         print(f"Plant ID: {plant["ps_id"]}, Name: {plant["ps_name"]}")
else:
   print("No plants found.")
   return

print("\nFetching detailed information for each plant...\n")
plant_ids = [str(plant["ps_id"]) for plant in plant_list]
plant_details = await plants_api.async_get_plant_details(plant_ids)
for plant in plant_details:
   print(f"Details for Plant ID {plant["ps_id"]}: {plant}")

print("\nFetching real-time data for each plant...\n")
real_time_data = await plants_api.async_get_realtime_data(plant_ids)
for plant_id, data in real_time_data.items():
   # Print only the data points where value is not None
   data_values = {k: v for k, v in data.items() if v and v.get("value") is not None}
   print(f"Real-time data for Plant ID {plant_id}: {data_values}")
```

The `Auth` class keeps the access between calls and refreshes it when needed. If you prefer to manage this state yourself, you can create your own subclass of `AbstractAuth`.

## Grid Control

The `Control` class enables retrieving and updating grid control settings. Parameters and value sets are documented in the iSolarCloud Developer portal.

### Example

```python
from pysolarcloud.control import Control
from pysolarcloud.plants import DeviceType

devices = await plants_api.async_get_plant_devices(plant_id, device_types=[DeviceType.ENERGY_STORAGE_SYSTEM])
device_uuid = devices[0]["uuid"]
control_api = Control(auth)
# Fetch current config
current_settings = await control_api.async_read_parameters(device_uuid)
print(current_settings)
# Make an update using the canonical command values.
# energy_management_mode (10003) must leave Self-consumption for charge/discharge to actuate:
# 0 self-consumption, 2 compulsory/forced, 3 external energy dispatch, 4 VPP.
await control_api.async_update_parameters(
    device_uuid,
    {
        "energy_management_mode": Control.encode_parameter("energy_management_mode", "compulsory"),
        "charge_discharge_command": Control.CHARGE_DISCHARGE_COMMANDS["charge"],
        "charge_discharge_power": "2500",
    },
)

# When using External EMS / forced dispatch, send a heartbeat periodically.
# 10017 = external_ems_heartbeat, value is the heartbeat interval in seconds (1-1000).
# Appendix 10: send the heartbeat when switching EMS modes through the API.
await control_api.async_heartbeat(device_uuid, interval_seconds=60)
```

# User-account login (unofficial, experimental)

In addition to the official OpenAPI OAuth flow (`Auth`), this fork provides `UserAuth`,
which logs in with a normal iSolarCloud **user account** (email + password) via the
reverse-engineered app/web API — no developer application required.

```python
from pysolarcloud import Server, UserAuth

async with UserAuth(Server.Europe, "you@example.com", "password") as auth:
    plants = await auth.async_get_plants()
    print(plants)
```

> ⚠️ **Unofficial and experimental.** This is not Sungrow's documented OpenAPI. It may
> change or break without notice and its use may be subject to Sungrow's terms of
> service. The protocol was reimplemented clean-room from the MIT-licensed
> [homebridge-platform-isolarcloud](https://github.com/MortJC/homebridge-platform-isolarcloud)
> (see `NOTICE`). Credentials are only sent to iSolarCloud over TLS and are never logged.

## User-account read helpers

`UserAuth` mirrors the iSolarCloud app's read API. All of the following are **read-only**;
endpoint paths and parameter *names* were verified against the app, but where the app uses
undocumented enum values (e.g. `date_type`, `query_type`, fault type/share codes) those are
left as caller-supplied optional arguments and documented as unverified against a live device.

| Method | App endpoint |
| --- | --- |
| `async_get_plants()` | `getPsList` |
| `async_get_plant_detail(ps_id)` | `getPsDetailWithPsType` (realtime household view) |
| `async_get_plant_detail_daily(ps_id, date_id)` | `getPsDetail` (daily view) |
| `async_get_devices(ps_id)` | `queryDeviceList` |
| `async_get_device_realtime(ps_key, *, point_ids=None)` | `queryDeviceRealTimeDataByPsKeys` |
| `async_get_historical_data(...)` | `queryMutiPointDataList` |
| `async_get_charging_piles(ps_id)` / `async_get_charge_pile_overview(ps_id)` | `getChargingPileList` / `getChargePileOverviewInfo` |
| `async_get_charging_pile_realtime(uuid)` / `async_get_charging_pile_last_data(uuid)` | `getChargingPileRealData` / `getChargingPileLastData` |
| `async_get_charging_pile_property(uuid, point_id)` | `getChargingPileProperty` |
| `async_get_battery_capacity(ps_id)` / `async_get_soc_by_ps_id(ps_id)` / `async_get_soc_by_sn(bt_sn)` | `getBatteryCapacityByPsIdV2` / `querySocByPsId` / `querySocBySn` |
| `async_get_battery_info(ps_id, ...)` | `getPsBatteryInfo` |
| `async_get_fault_count(ps_id)` / `async_query_faults(...)` / `async_get_fault_detail(fault_code)` | `getDevFaultCountByPsId` / `queryFaultList` / `getFaultDetail` |
| `async_get_open_fault_num()` / `async_get_unread_fault_count(...)` | `getPsOpenFaultNum` / `getNotReadFaultCount` |
| `async_get_household_storage_report(ps_id, ...)` / `async_get_energy_summary(ps_id, ...)` | `getHouseholdStoragePsReport` / `getPsEnergySummaryInfo` |
| `async_get_device_day_month_year_history(ps_key, ...)` / `async_get_device_minute_history(ps_key, ...)` | `queryDevicePointsDayMonthYearDataList` / `queryDevicePointMinuteDataList` |

> ⚠️ **Breaking change in 0.16.0** — `async_get_device_realtime` now takes a device
> `ps_key` (from `async_get_devices`) as its single positional argument instead of
> `(ps_id, device_sn)`, and returns data keyed by device `uuid`
> (`{uuid: {point_id: {"id", "value", "unit", "name"}}}`) instead of a flat
> `{point_id: {"value", "unit"}}` map. It now posts to `queryDeviceRealTimeDataByPsKeys`;
> the previous `/v1/devService/queryDevice` path did not exist in the app. Migrate by
> passing the device `ps_key` and reading points under each `uuid`.

# Contributions
Ideas or contributions are welcome. I am not affiliated with Sungrow, I'm just another user of the API. My main use case will be a HomeAssistant integration based on this package.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, the checks CI runs, and the
rules that `main` enforces — notably that **commits must be signed** and that pull requests
need the branch up to date with `main`.

Enjoy!
