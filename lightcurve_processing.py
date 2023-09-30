"""Mihir Patankar [mpatankar06@gmail.com]"""
import uuid
from abc import ABC, abstractmethod
from collections import namedtuple
from io import StringIO
from pathlib import Path

import matplotlib
from astropy import io, table

# pylint: disable-next=no-name-in-module
from ciao_contrib.runtool import dmcopy, dmextract, dmkeypar, dmlist, dmstat, new_pfiles_environment
from matplotlib import pyplot
from pandas import DataFrame
import postage_stamp_plotter
from data_structures import LightcurveParseResults, Message, ObservationData, ObservationHeaderInfo


class ObservationProcessor(ABC):
    """Base class for observation processor implementations for different Chandra instruments."""

    def __init__(self, event_list_file, source_region_file, message_collection_queue, config):
        self.event_list = Path(event_list_file)
        self.sky_region_qualifier = f"[sky=region({source_region_file})]"
        self.detector_coords_image, self.sky_coords_image = None, None
        self.message_collection_queue = message_collection_queue
        self.binsize = config["Binsize"]
        self.minimum_counts = config["Minimum Counts"]

    def process(self):
        """Sequence in which all steps of the processing routine are called."""

        message_uuid = uuid.uuid4()
        with new_pfiles_environment():
            observation_id = dmkeypar(infile=f"{self.event_list}", keyword="OBS_ID", echo=True)
            prefix = f"Observation {observation_id}: "

            def status(status):
                self.message_collection_queue.put(Message(f"{prefix}{status}", message_uuid))

            status("Retrieving images...")
            self.get_images()
            status("Extracting lightcurves...")
            lightcurves = self.extract_lightcurves(
                self.event_list, self.sky_region_qualifier, self.binsize
            )
            status("Exporting columns...")
            filtered_lightcurves = self.export_lightcurve_columns(lightcurves)
            status("Plotting lightcurves...")
            parse_results = self.plot(filtered_lightcurves)
            status("Plotting lightcurves... Done")

        return parse_results

    @staticmethod
    @abstractmethod
    def extract_lightcurves(event_list, sky_region_qualifier, binsize):
        """Extract lightcurve(s) from an event list, one should pass in one with a specific source
        region extracted."""

    @staticmethod
    @abstractmethod
    def export_lightcurve_columns(lightcurves: list[Path]):
        """Filter lightcurve(s) to get the columns we care about and format them."""

    @abstractmethod
    def plot(self, lightcurves: list[Path]) -> LightcurveParseResults:
        """Parse lightcurve(s). Returns what will then be returned by the thread pool future."""

    def get_images(self):
        """Gets the images from the event list, one in sky coordinates, the other in detector
        coordinates. This is useful to be able to track if a lightcurve dip corresponds with a
        source going over the edge of the detector. The image is cropped otherwise we would be
        dealing with hundreds of thousands of blank pixels. The cropping bounds are written to the
        FITS file for later use in plotting."""
        CropBounds = namedtuple("CropBounds", ["x_min", "y_min", "x_max", "y_max"])

        def convert_bounds_object_to_hdu(bounds):
            hdu = io.fits.table_to_hdu(
                table.Table({field: [(getattr(bounds, field))] for field in bounds._fields})
            )
            hdu.header["EXTNAME"] = "BOUNDS"
            return hdu

        dmstat(infile=f"{self.event_list}[cols x,y]")
        sky_bounds = CropBounds(*dmstat.out_min.split(","), *dmstat.out_max.split(","))
        dmcopy(
            infile=f"{self.event_list}{self.sky_region_qualifier}"
            f"[bin x={sky_bounds.x_min}:{sky_bounds.x_max}:0.5,"
            f"y={sky_bounds.y_min}:{sky_bounds.y_max}:0.5]",
            outfile=(sky_coords_image := f"{self.event_list}.skyimg.fits"),
        )
        with io.fits.open(sky_coords_image, mode="append") as hdu_list:
            hdu_list.append(convert_bounds_object_to_hdu(sky_bounds))
        dmstat(infile=f"{self.event_list}[cols detx,dety]")

        detector_bounds = CropBounds(*dmstat.out_min.split(","), *dmstat.out_max.split(","))
        dmcopy(
            infile=f"{self.event_list}{self.sky_region_qualifier}"
            f"[bin detx={detector_bounds.x_min}:{detector_bounds.x_max}:0.5,"
            f"dety={detector_bounds.y_min}:{detector_bounds.y_max}:0.5]",
            outfile=(detector_coords_image := f"{self.event_list}.detimg.fits"),
        )
        with io.fits.open(detector_coords_image, mode="append") as hdu_list:
            hdu_list.append(convert_bounds_object_to_hdu(detector_bounds))
        self.sky_coords_image, self.detector_coords_image = sky_coords_image, detector_coords_image

    def get_observation_details(self):
        """Gets keys from the header block detailing the observation information."""
        return ObservationHeaderInfo(
            instrument=dmkeypar(infile=f"{self.event_list}", keyword="INSTRUME", echo=True),
            observation_id=dmkeypar(infile=f"{self.event_list}", keyword="OBS_ID", echo=True),
            region_id=dmkeypar(infile=f"{self.event_list}", keyword="REGIONID", echo=True),
            start_time=dmkeypar(infile=f"{self.event_list}", keyword="DATE-OBS", echo=True),
            end_time=dmkeypar(infile=f"{self.event_list}", keyword="DATE-END", echo=True),
        )


class AcisProcessor(ObservationProcessor):
    """Processes observations produced by the ACIS (Advanced CCD Imaging Spectrometer) instrument
    aboard Chandra."""

    ENERGY_LEVELS = {
        "broad": "energy=300:7000",
        "soft": "energy=300:1200",
        "medium": "energy=1200:2000",
        "hard": "energy=2000:7000",
    }

    @staticmethod
    def adjust_binsize(event_list, binsize):
        """For ACIS, time resolution can be in the seconds in timed exposure mode, as compared to in the microseconds for HRC."""
        # TODO https://cxc.cfa.harvard.edu/ciao/ahelp/times.html find out the difference between interleaved and standard TIME mode
        time_resolution = float(dmkeypar(infile=str(event_list), keyword="TIMEDEL", echo=True))
        return binsize // time_resolution * time_resolution

    @staticmethod
    def extract_lightcurves(event_list, sky_region_qualifier, binsize):
        outfiles = []
        for light_level, energy_range in AcisProcessor.ENERGY_LEVELS.items():
            dmextract(
                infile=f"{event_list}{sky_region_qualifier}[{energy_range}][bin time=::"
                f"{AcisProcessor.adjust_binsize(event_list, binsize)}]",
                outfile=(outfile := f"{event_list}.{light_level}.lc"),
                opt="ltc1",
            )
            outfiles.append(Path(outfile))
        return outfiles

    @staticmethod
    def export_lightcurve_columns(lightcurves):
        outfiles = []
        for lightcurve in lightcurves:
            dmlist(
                infile=f"{lightcurve}"
                f"[cols time,count_rate,count_rate_err,counts,exposure,area]",
                opt="data,clean",
                outfile=(outfile := f"{lightcurve}.ascii"),
            )
            outfiles.append(Path(outfile))
        return outfiles

    def plot(self, lightcurves):
        lightcurve_data: dict[str, DataFrame] = {
            energy_level: table.Table.read(lightcurve, format="ascii").to_pandas()
            for energy_level, lightcurve in zip(AcisProcessor.ENERGY_LEVELS.keys(), lightcurves)
        }
        # Trim zero exposure points
        for energy_level, lightcurve_dataframe in lightcurve_data.items():
            lightcurve_data[energy_level] = lightcurve_dataframe[
                lightcurve_dataframe["EXPOSURE"] != 0
            ]
        # The type casts are important as the data is returned by CIAO as NumPy data types.
        observation_data = ObservationData(
            average_count_rate=float(round(lightcurve_data["broad"]["COUNT_RATE"].mean(), 3)),
            total_counts=int(lightcurve_data["broad"]["COUNTS"].sum()),
            total_exposure_time=float(round(lightcurve_data["broad"]["EXPOSURE"].sum(), 3)),
            raw_start_time=int(lightcurve_data["broad"]["TIME"].min()),
        )
        # This data is just so people can view the exact numerical data that was plotted.
        output_plot_data = StringIO(lightcurve_data["broad"].to_string())
        return LightcurveParseResults(
            observation_header_info=self.get_observation_details(),
            observation_data=observation_data,
            plot_csv_data=output_plot_data,
            plot_svg_data=self.create_plot(lightcurve_data),
            postagestamp_png_data=postage_stamp_plotter.plot_postagestamps(
                self.sky_coords_image, self.detector_coords_image
            ),
        )

    @staticmethod
    def create_plot(lightcurve_data: dict[str, DataFrame]):
        """Generate a pyplot plot to model the lightcurves."""
        matplotlib.use("svg")
        initial_time = lightcurve_data["broad"]["TIME"].min()
        zero_shifted_time_kiloseconds = (lightcurve_data["broad"]["TIME"] - initial_time) / 1000
        observation_duration = zero_shifted_time_kiloseconds.max()
        figure, (broad_plot, seperation_plot) = pyplot.subplots(
            nrows=2, ncols=1, figsize=(10, 5), constrained_layout=True, sharey=True
        )
        broad_plot.errorbar(
            x=zero_shifted_time_kiloseconds,
            y=lightcurve_data["broad"]["COUNT_RATE"],
            yerr=lightcurve_data["broad"]["COUNT_RATE_ERR"],
            color="red",
            marker="s",
            markerfacecolor="black",
            markersize=4,
            ecolor="black",
            markeredgecolor="black",
            capsize=3,
        )
        broad_plot.set_xlim([0, observation_duration])
        seperated_light_level_colors = {"soft": "lightsalmon", "medium": "red", "hard": "firebrick"}
        for light_level, color in seperated_light_level_colors.items():
            seperation_plot.plot(
                zero_shifted_time_kiloseconds,
                lightcurve_data[light_level]["COUNT_RATE"],
                color=color,
                label=light_level.capitalize(),
            )
        seperation_plot.legend(
            loc="upper center",
            bbox_to_anchor=(0.5, 1.05),
            ncol=3,
            frameon=False,
            fontsize=14,
        )
        seperation_plot.set_xlim([0, observation_duration])
        figure.supylabel("Count Rate (counts/second)")
        figure.supxlabel("Time (kiloseconds)")
        figure.suptitle("Lightcurve in Broadband and Separated Energy Bands")
        pyplot.savefig(svg_data := StringIO(), bbox_inches="tight")
        pyplot.close(figure)
        return svg_data


class HrcProcessor(ObservationProcessor):
    """Processes for observations produced by the HRC (High Resolution Camera) instrument aboard
    Chandra."""

    @staticmethod
    def extract_lightcurves(event_list, binsize):
        raise NotImplementedError()

    @staticmethod
    def export_lightcurve_columns(lightcurves):
        raise NotImplementedError()

    def plot(self, lightcurves):
        raise NotImplementedError()
