# Home Assistant integration for AF Bostäder

Custom integration that logs in to [afbostader.se](https://www.afbostader.se) and exposes your laundry bookings as sensors in Home Assistant.

## Entities

| Entity | State | Attributes |
|---|---|---|
| `sensor.af_bostader_tvattstuga_next_laundry_booking` | Start time of your next booked pass (timestamp) | `end`, `group`, `location`, `booking_id`, `count`, `bookings` |
| `sensor.af_bostader_tvattstuga_next_available_laundry_time` | Start time of the next free pass in any laundry room you can book | `end`, `group`, `location`, `count`, `available_slots` (next 10) |

When there's no booking or no free pass, the state is `unknown`. Data refreshes every 10 minutes.

## Installation

**HACS:** add this repository as a custom repository (category *Integration*) and install it.

**Manual:** copy `custom_components/afbostader` to `<config>/custom_components/afbostader` and restart Home Assistant.

Then go to *Settings → Devices & services → Add integration → AF Bostäder Tvättstuga* and log in with the email and password you use on afbostader.se.

## How it works

1. Logs in through the normal login form on afbostader.se (ASP.NET WebForms postback).
2. Opens *Dina sidor → Boka tvättid* (`/dina/sidor/boka-tvattid/`).
3. Calls the same JSON booking service as the website (`afbostader.se/bookingservice/bookingservice.svc/`):
   `GetCustomerBookings`, `GetCustomerGroups` and `GetCalendarData`.

The integration only reads data. It never books or cancels anything.

