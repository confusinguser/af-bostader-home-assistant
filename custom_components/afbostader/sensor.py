"""Sensors for AF Bostäder laundry booking."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import LaundryData, LaundrySlot
from .const import DOMAIN, MAX_ATTRIBUTE_SLOTS
from .coordinator import AfbConfigEntry, AfbCoordinator


@dataclass(frozen=True, kw_only=True)
class AfbSensorDescription(SensorEntityDescription):
    """Describes an AF Bostäder sensor."""

    slots_fn: Callable[[LaundryData], list[LaundrySlot]]
    list_attribute: str


SENSORS: tuple[AfbSensorDescription, ...] = (
    AfbSensorDescription(
        key="next_booking",
        translation_key="next_booking",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:washing-machine",
        slots_fn=lambda data: data.bookings,
        list_attribute="bookings",
    ),
    AfbSensorDescription(
        key="next_available",
        translation_key="next_available",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-clock",
        slots_fn=lambda data: data.available,
        list_attribute="available_slots",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AfbConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(AfbSensor(coordinator, description) for description in SENSORS)


class AfbSensor(CoordinatorEntity[AfbCoordinator], SensorEntity):
    """Timestamp sensor for the next booked / available laundry pass."""

    _attr_has_entity_name = True
    entity_description: AfbSensorDescription

    def __init__(self, coordinator: AfbCoordinator, description: AfbSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name="AF Bostäder Tvättstuga",
            manufacturer="AF Bostäder",
            model="Aptus tvättbokning",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.afbostader.se/dina/sidor/boka-tvattid/",
        )

    @property
    def _slots(self) -> list[LaundrySlot]:
        return self.entity_description.slots_fn(self.coordinator.data)

    @property
    def native_value(self) -> datetime | None:
        slots = self._slots
        return slots[0].start if slots else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        slots = self._slots
        attrs: dict[str, Any] = {"count": len(slots)}
        if slots:
            first = slots[0]
            attrs["end"] = first.end.isoformat()
            attrs["group"] = first.group_name
            attrs["location"] = first.location_name
            if first.booking_id:
                attrs["booking_id"] = first.booking_id
        attrs[self.entity_description.list_attribute] = [
            s.as_dict() for s in slots[:MAX_ATTRIBUTE_SLOTS]
        ]
        return attrs
