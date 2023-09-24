"""Mihir Patankar [mpatankar06@gmail.com]"""
from io import BytesIO, StringIO
from pathlib import Path
from tkinter.ttk import Checkbutton, Entry, Label
from typing import Callable, NamedTuple
from uuid import UUID


class ConfigField(NamedTuple):
    """Holds immutable data for a GUI configuration entry."""

    label: Label
    field: Entry | Checkbutton
    default_value: str | bool
    entry_type: Callable


class Message(NamedTuple):
    """Holds an optional uuid field, this is used for when you want to track and update a single
    message in the buffer rather than constantly appending."""

    content: str
    uuid: UUID = None


class DataProducts(NamedTuple):
    """Holds file paths for data products."""

    event_list_file: Path = None
    source_region_file: Path = None
    region_image_file: Path = None

    def __reduce__(self):
        return (
            self.__class__,
            (str(self.event_list_file), str(self.source_region_file), str(self.region_image_file)),
        )


class ObservationHeaderInfo(NamedTuple):
    """Holds details derrived from the observation header."""

    instrument: str
    observation_id: int
    region_id: int
    start_time: str
    end_time: str


class ObservationData(NamedTuple):
    """Holds data derrived from the observation event list."""

    average_count_rate: float
    total_counts: int
    total_exposure_time: float
    raw_start_time: int


class LightcurveParseResults(NamedTuple):
    """Holds observation details and data along with the in-memory svg string representation for
    its plot image."""

    observation_header_info: ObservationHeaderInfo
    observation_data: ObservationData
    plot_csv_data: StringIO
    plot_svg_data: StringIO
    postagestamp_png_data: BytesIO


class ExportableObservationData(NamedTuple):
    """Combines all observation data into the form that will be sent to the templating engine."""

    columns: dict
    plot_image_path: str
    postage_stamp_image_path: str
