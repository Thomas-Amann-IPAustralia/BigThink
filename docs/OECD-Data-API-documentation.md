Last updated: 22 July 2024

# OECD Data API documentation

## **About the API**

The OECD provides programmatic access to OECD data for OECD countries and selected non-member
economies through a RESTful application programming interface (API) based on the SDMX standard.
The APIs allow developers to easily query the OECD data in several ways to create innovative software
applications which use dynamically updated OECD data.

Note that the RESTful SDMX API standard has different versions that are partly supported by the OECD
API instance.

## **Terms and conditions**

OECD data and the API service are offered subject to your acceptance of OECD <u>[Terms and Conditions.](https://www.oecd.org/termsandconditions/)</u>

## **What is the SDMX standard?**

OECD and other international organisations have defined an ISO standard format for describing and
transmitting statistical data, which can be used in internet-based electronic communications with
users and third parties. The SDMX API standard supports JSON, XML and CSV formats. Please see
details below.

In SDMX the measurement of a phenomenon (e.g. a population count) is known as an "observation".
Observations are described and uniquely identified by a combination of "dimension" values (e.g. a
country and a year). "Attributes" allow further adding useful information but do not help identifying
statistical data (e.g. observation status). Observations of a same kind - identifiable by the same
dimensions - are grouped into a "dataset". The SDMX-ML and SDMX-JSON formats also allows for an
optional intermediate grouping of the observations for all values of one of the dimensions, called
"series". Specifically, the grouping of the observations for all available time periods is a so-called "time
series" (e.g. the population counts for all years for a specific country). Similarly, groups can be made
with any dimension. Alternatively, no grouping results in a flat list of all observations in the dataset.
Descriptive information on the dataset, dimensions and attributes is called "structural metadata". It
is returned as response to structure queries and also within the "structure" part of the SDMX-JSON
response to data queries.

## **Specificities of the SDMX-JSON format**

The structural metadata for the actually returned observations are included in the SDMX-JSON
response message. If possible, in order to minimise repetition, each dimension/attribute is specified

1 ©OECD 2024

Unclassified - Non classifié

Last updated: 22 July 2024

at the highest possible grouping level. Dimensions and attributes specified on dataset and series level
have the same values for all observations throughout the dataset or series respectively.

To uniquely identify observations in the SDMX-JSON message, the indexes of the corresponding
dimension values as defined in the "structure" part of the message at series and observation level are
concatenated into the series' or observation's property name. Here, the indexes are ordered in the
pre-defined order of dimensions as defined in the "structure" part of the message and separated
between each other by a colon character.

The concrete values of attributes at series and observation level are also returned through their index
as defined in the "structure" part of the message.

## **Specificities of the SDMX-CSV format**

This RFC 4180 compatible format is limited to transmitting data in a flattened table.

In order to ensure the identifiability of the data contained in the message, the header row contains
the column headers. After this header row, each following row contains the information (dimension
values, observation value, attribute values) related to one specific observation or to one or more
attribute values attached to the values of a subset of dimensions. Advanced queries for database
synchronisation purposes allow also retrieving information about data that has been previously
deleted. In such messages, a row can also concern several observations if dimension values are
omitted.

Even if csv stands for 'comma-separated values', SDMX-CSV allows using a localised field separator,
e.g., the semi-colon ';', depending on the locale of the client (as indicated in the http Accept-Language
header). Note that the separator used in a message can be determined by retrieving the character
that follows the fixed first column header term STRUCTURE or DATAFLOW (which may be extended
by a squared bracket term).

## **Syntax for querying data or reference metadata**

To create a data or reference metadata query, the following parameters must be supplied in an URL
in the following format: an agency identifier, a dataflow identifier, a dataflow version, a list of
dimension values when using a filter or and some optional additional parameters:

**SDMX API version 1**

**<mark>https://sdmx.oecd.org/public/rest/data</mark>** <mark>/<agency identifier>,<dataflow identifier>,<dataflow</mark>
version>/<filter expression>[?<optional parameters>]

**SDMX API version 2**

**<mark>https://sdmx.oecd.org/public/rest/v2/data/dataflow/</mark>** <mark><agency identifier>/<dataflow</mark>
identifier>/<dataflow version>/<filter expression>[?<optional parameters>]

2 ©OECD 2024

Unclassified - Non classifié

Last updated: 22 July 2024

<mark>Notes:</mark>

<mark>-</mark> <mark>The</mark> _<mark>dataflow</mark>_ <mark>in SDMX is a slice or partial view of a potentially bigger dataset or data cube.</mark>
<mark>Clients can still understand it as a self-contained classical statistical dataset. It is fully identified</mark>
<mark>by the triplet: agency identifier, dataflow identifier and dataflow version.</mark>

<mark>-</mark> **<mark>Reference metadata</mark>** <mark>can only be queried using the SDMX API version 2.</mark>

**Parameter** **Use**

**agency**
The identifier of the agency owning the dataflow to be queried.
**<mark>identifier</mark>**

**dataflow**
**version**

The version of the structural definition of the dataflow to be queried.
If left empty (for SDMX API version 1) or replaced by '+' (for SDMX API version 2),
then the currently **latest** dataflow version is used. While this often allows getting
newer data, be aware that new dataflow versions may contain non-backwardcompatible structural changes.

3 ©OECD 2024

Unclassified - Non classifié

Last updated: 22 July 2024

**Optional Parameter** **Use**

**Only SDMX API version 1**

The start time period for which results should be supplied (inclusive). If
not specified, data returned from the beginning. The value can be
expressed using dateTime, Gregorian time periods or SDMX reporting
periods.

Examples:

**startPeriod**

- '2015'

- '2015-A1'

- '2015-S1'

- '2015-Q1'

- '2015-M01'

- '2015-01'

- '2015-01-01'

- '2015-01-01T00:00:00'

**c[TIME_PERIOD]**

**Only SDMX API version 2**

Filter data by Time Period. A list of 2 time periods separated by the plus
character '+' and the comparison operators 'ge' (greater or equal than)
and 'le'' (lower or equal than) can be used with dateTime, Gregorian time
periods or SDMX reporting periods to set the start and end periods.

Example:

  - 'ge:2018+le:2024'

**dimensionAtObservation**

The identifier of the dimension to be presented at the observation level
for the purpose of grouping observations into 'series', or 'AllDimensions'
for a flat representation of the observations. If this parameter is not set,
then the default value order is:

  - 'TIME_PERIOD: grouping of observations into time series

  - 'AllDimensions': if the data has no Time Period dimension

4 ©OECD 2024

Unclassified - Non classifié

Last updated: 22 July 2024

**attributes**

**Only SDMX API version 2**

This parameter specifies which attribute values are to be returned.
Possible options are:

  - 'all': the values of all normal attributes (defined in the data
structure) and of all reference metadata attributes (defined in
the metadata structure) are returned

  - 'none': the values of normal attributes and of reference
metadata attributes are not returned

  - 'dsd': only the values of all normal attributes are returned

  - 'msd': only the values of all reference metadata attributes are
returned

**updatedAfter**

**Only for SDMX-CSV v2 (see below)**

If this parameter is used, the returned response only includes the
observations inserted, updated or deleted since that point in time. The
value can be expressed using dateTime including the client's time zone:

  - '2015-12-31T23:59:59.9999-01:00'

We strongly recommend using this parameter to reduce the amount of
data to be transferred in scenarios of frequent database
synchronisations.

5 ©OECD 2024

Unclassified - Non classifié

Last updated: 22 July 2024

[For more information about SDMX API version 1, see here.](https://github.com/sdmx-twg/sdmx-rest/blob/v1.5.0/v2_1/ws/rest/docs/4_4_data_queries.md)

[For more information about SDMX API version 2, see here.](https://github.com/sdmx-twg/sdmx-rest/blob/v2.0.0/doc/data.md)

**Examples:**

SDMX API version 1:

Observations and their normal attributes
<u>[https://sdmx.oecd.org/public/rest/data/OECD.ENV.EPI,DSD_ECH@EXT_DROUGHT,1.0/AFG+BFA.A.E](https://sdmx.oecd.org/public/rest/data/OECD.ENV.EPI,DSD_ECH@EXT_DROUGHT,1.0/AFG+BFA.A.ED_CROP_IND.....?startPeriod=1981&endPeriod=2021&dimensionAtObservation=AllDimensions&updatedAfter=2015-01-01T00:00:00.000-01:00)</u>
<u>[D_CROP_IND.....?startPeriod=1981&endPeriod=2021&dimensionAtObservation=AllDimensions&upd](https://sdmx.oecd.org/public/rest/data/OECD.ENV.EPI,DSD_ECH@EXT_DROUGHT,1.0/AFG+BFA.A.ED_CROP_IND.....?startPeriod=1981&endPeriod=2021&dimensionAtObservation=AllDimensions&updatedAfter=2015-01-01T00:00:00.000-01:00)</u>
<u>[atedAfter=2015-01-01T00:00:00.000-01:00](https://sdmx.oecd.org/public/rest/data/OECD.ENV.EPI,DSD_ECH@EXT_DROUGHT,1.0/AFG+BFA.A.ED_CROP_IND.....?startPeriod=1981&endPeriod=2021&dimensionAtObservation=AllDimensions&updatedAfter=2015-01-01T00:00:00.000-01:00)</u>

SDMX API version 2:

Observations and their normal attributes
<u>[https://sdmx.oecd.org/public/rest/v2/data/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/1.0/](https://sdmx.oecd.org/public/rest/v2/data/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/1.0/AFG.A.ED_CROP_IND.*.*.*.*.*?c%5bTIME_PERIOD%5d=ge:1981+le:2021&attributes=dsd&measures=all&updatedAfter=2015-01-01T00:00:00.000-01:00)</u>
<u>[AFG.A.ED_CROP_IND.*.*.*.*.*?c[TIME_PERIOD]=ge:1981+le:2021&attributes=dsd&measures=all&u](https://sdmx.oecd.org/public/rest/v2/data/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/1.0/AFG.A.ED_CROP_IND.*.*.*.*.*?c%5bTIME_PERIOD%5d=ge:1981+le:2021&attributes=dsd&measures=all&updatedAfter=2015-01-01T00:00:00.000-01:00)</u>
<u>[pdatedAfter=2015-01-01T00:00:00.000-01:00](https://sdmx.oecd.org/public/rest/v2/data/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/1.0/AFG.A.ED_CROP_IND.*.*.*.*.*?c%5bTIME_PERIOD%5d=ge:1981+le:2021&attributes=dsd&measures=all&updatedAfter=2015-01-01T00:00:00.000-01:00)</u>

Observations and their normal attributes, latest dataflow version
<u>[https://sdmx.oecd.org/public/rest/v2/data/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/+/A](https://sdmx.oecd.org/public/rest/v2/data/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/+/AUS.A.ED_CROP_ANOM.*.*.*.*.*?c%5bTIME_PERIOD%5d=ge:2018+le:2021&attributes=dsd&measures=all&updatedAfter=2015-01-01T00:00:00.000-01:00)</u>
<u>[US.A.ED_CROP_ANOM.*.*.*.*.*?c[TIME_PERIOD]=ge:2018+le:2021&attributes=dsd&measures=all&](https://sdmx.oecd.org/public/rest/v2/data/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/+/AUS.A.ED_CROP_ANOM.*.*.*.*.*?c%5bTIME_PERIOD%5d=ge:2018+le:2021&attributes=dsd&measures=all&updatedAfter=2015-01-01T00:00:00.000-01:00)</u>
<u>[updatedAfter=2015-01-01T00:00:00.000-01:00](https://sdmx.oecd.org/public/rest/v2/data/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/+/AUS.A.ED_CROP_ANOM.*.*.*.*.*?c%5bTIME_PERIOD%5d=ge:2018+le:2021&attributes=dsd&measures=all&updatedAfter=2015-01-01T00:00:00.000-01:00)</u>

Reference metadata attributes
<u>[https://sdmx.oecd.org/public/rest/v2/data/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/1.0/](https://sdmx.oecd.org/public/rest/v2/data/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/1.0/AFG.A.ED_CROP_IND.*.*.*.*.*?c%5bTIME_PERIOD%5d=ge:1981+le:2021&attributes=msd&measures=none&updatedAfter=2015-01-01T00:00:00.000-01:00)</u>
<u>[AFG.A.ED_CROP_IND.*.*.*.*.*?c[TIME_PERIOD]=ge:1981+le:2021&attributes=msd&measures=none](https://sdmx.oecd.org/public/rest/v2/data/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/1.0/AFG.A.ED_CROP_IND.*.*.*.*.*?c%5bTIME_PERIOD%5d=ge:1981+le:2021&attributes=msd&measures=none&updatedAfter=2015-01-01T00:00:00.000-01:00)</u>
<u>[&updatedAfter=2015-01-01T00:00:00.000-01:00](https://sdmx.oecd.org/public/rest/v2/data/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/1.0/AFG.A.ED_CROP_IND.*.*.*.*.*?c%5bTIME_PERIOD%5d=ge:1981+le:2021&attributes=msd&measures=none&updatedAfter=2015-01-01T00:00:00.000-01:00)</u>

## **Syntax for querying data structures**

<mark>To create a data structure query, a dataset identifier and agency name must be supplied in an URL in</mark>
the following format:

**SDMX API version 1**

**<mark>https://sdmx.oecd.org/public/rest/dataflow/</mark>** <mark><agency identifier>/<dataflow identifier>/<version</mark>
number> **?references=all&detail=referencepartial**

**SDMX API version 2**

**<mark>https://sdmx.oecd.org/public/rest/v2/structure/dataflow/</mark>** <mark><agency identifier>/<dataflow</mark>
identifier>/<version number> **?references=all&detail=referencepartial**

<mark>For the definition of these parameters please see the above section on the syntax for querying data.</mark>

[For more information about SDMX structure API version 1, see here.](https://github.com/sdmx-twg/sdmx-rest/blob/v1.5.0/v2_1/ws/rest/docs/4_3_structural_queries.md)

[For more information about SDMX structure API version 2, see here.](https://github.com/sdmx-twg/sdmx-rest/blob/v2.0.0/doc/structures.md)

6 ©OECD 2024

Unclassified - Non classifié

Last updated: 22 July 2024

**Examples:**

SDMX API version 1:

<u><mark>[https://sdmx.oecd.org/public/rest/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/1.0?referenc](https://sdmx.oecd.org/public/rest/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/1.0?references=all&detail=referencepartial)</mark></u>
<u>[es=all&detail=referencepartial](https://sdmx.oecd.org/public/rest/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/1.0?references=all&detail=referencepartial)</u>

SDMX API version 2:

<u><mark>[https://sdmx.oecd.org/public/rest/v2/structure/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT](https://sdmx.oecd.org/public/rest/v2/structure/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/1.0?references=all&detail=referencepartial)</mark></u>
<u><mark>[/1.0?references=all&detail=referencepartial](https://sdmx.oecd.org/public/rest/v2/structure/dataflow/OECD.ENV.EPI/DSD_ECH@EXT_DROUGHT/1.0?references=all&detail=referencepartial)</mark></u>

## **API query builder**

<mark>Data and structure queries can be generated using the</mark> **<mark>Developer API</mark>** <mark>feature of the</mark> <u><mark>[OECD Data](https://data-explorer.oecd.org/)</mark></u>
<u><mark>[Explorer.](https://data-explorer.oecd.org/)</mark></u>

## **Requesting specific response formats: XML, JSON or CSV**

<mark>The wished response format (SDMX-ML, SDMX-JSON or SDMX-CSV), content-languages and</mark>
compression settings are usually provided to the API through HTTP content negotiation.

**Selection of the Appropriate Representation**

<mark>Use one of the following values for the 'Accept' header for</mark> **<mark>data</mark>** <mark>and</mark> **<mark>reference metadata</mark>** <mark>queries:</mark>

<mark>•</mark> <mark>SDMX-ML v2.1 generic data format (obsolete): 'application/vnd.sdmx.genericdata+xml;</mark>
<mark>charset=utf-8; version=2.1'</mark>

  - SDMX-ML v2.1 structure-specific data format:
'application/vnd.sdmx.structurespecificdata+xml; charset=utf-8; version=2.1'

  - SDMX-ML v3 structure-specific data format (experimental):
'application/vnd.sdmx.structurespecificdata+xml; charset=utf-8; version=3.0'

<mark>•</mark> <mark>SDMX-JSON v1: 'application/vnd.sdmx.data+json; charset=utf-8; version=1.0'</mark>

<mark>•</mark> <mark>SDMX-JSON v2: 'application/vnd.sdmx.data+json; charset=utf-8; version=2'</mark>

<mark>•</mark> <mark>SDMX-CSV v1: 'application/vnd.sdmx.data+csv; charset=utf-8'</mark>

<mark>•</mark> <mark>SDMX-CSV v2: 'application/vnd.sdmx.data+csv; charset=utf-8; version=2'</mark>

<mark>For SDMX-CSV, optionally add the settings:</mark>

<mark>•</mark> <mark>'; labels=both' to include the names of objects inside the response in addition to their</mark>
<mark>identifiers.</mark>

<mark>•</mark> <mark>'timeformat=</mark> <mark>normalized' to obtain a pivotable time period format</mark>

<mark>[Alternatively, it is possible to use the following non-SDMX standard 'format' URL parameter:](https://gitlab.com/sis-cc/eurostat-sdmx-ri/nsiws.net.mirrored/-/blob/master/doc/CONFIGURATION.md#format-configuration)</mark>

<mark>•</mark> <mark>SDMX-ML v2.1 generic data format (obsolete): 'genericdata'</mark>

<mark>•</mark> <mark>SDMX-ML v2.1 structure-specific data format: 'structurespecificdata'</mark>

<mark>•</mark> <mark>SDMX-JSON v2: 'jsondata'</mark>

<mark>•</mark> <mark>SDMX-CSV v1: 'csv'</mark>

<mark>•</mark> <mark>SDMX-CSV v1 as attached file: 'csvfile'</mark>

7 ©OECD 2024

Unclassified - Non classifié

Last updated: 22 July 2024

<mark>•</mark> <mark>SDMX-CSV v1 as attached file including the names of objects in addition to their identifiers:</mark>
<mark>'csvfilewithlabels'</mark>

**Note** : **Reference metadata** are only supported for SDMX-JSON v2 and SDMX-CSV v2.

<mark>Use one of the following values for the 'Accept' header for</mark> **<mark>structure</mark>** <mark>queries:</mark>

<mark>•</mark> <mark>SDMX-ML v2.0: 'application/vnd.sdmx.structure+xml; charset=utf-8; version=2.0'</mark>

<mark>•</mark> <mark>SDMX-ML v2.1: 'application/vnd.sdmx.structure+xml; charset=utf-8; version=2.1'</mark>

<mark>•</mark> <mark>SDMX-JSON v1: 'application/vnd.sdmx.structure+json; charset=utf-8; version=1.0'</mark>

Optionally add the setting '; urn=true' to include the URNs of each structure inside the response.

<mark>[Alternatively, it is possible to use the following non-SDMX standard 'format' URL parameter:](https://gitlab.com/sis-cc/eurostat-sdmx-ri/nsiws.net.mirrored/-/blob/master/doc/CONFIGURATION.md#format-configuration)</mark>

<mark>•</mark> <mark>SDMX-ML v2.0 and v2.1: 'structure'</mark>

<mark>[It is also possible to use the following non-SDMX standard 'formatVersion' URL parameter:](https://gitlab.com/sis-cc/eurostat-sdmx-ri/nsiws.net.mirrored/-/blob/master/doc/CONFIGURATION.md#format-configuration)</mark>

<mark>•</mark> <mark>SDMX-ML v2.0: '2.0'</mark>

<mark>•</mark> <mark>SDMX-ML v2.1: '2.1'</mark>

**Selection of the Appropriate language**

<mark>The 'Accept-Language' header is used to indicate the language preferences of the client. Multiple</mark>
values, along with their respective weights, are possible. For example:

Accept-Language: ru, en-gb;q=0.8, en;q=0.7

**Enabling data compression**

<mark>Standard compression methods can be enabled using the appropriate 'Accept-Encoding' header. We</mark>
<mark>strongly recommend using this feature systematically to minimise the usage of the internet band</mark>
width and increase the download speed. For example:

<mark>Accept-Encoding: gzip, deflate, br</mark>

## **Additional documentation & support**

<mark>The following resources describe the SDMX standard in more detail:</mark>

<mark>-</mark> <u><mark>[.Stat SDMX RESTful web service cheat sheet](https://sis-cc.gitlab.io/dotstatsuite-documentation/using-api/restful/)</mark></u>

<mark>-</mark> <u><mark>[SDMX RESTful API specifications](https://github.com/sdmx-twg/sdmx-rest)</mark></u>

<mark>-</mark> <u><mark>[SDMX-ML specifications](https://github.com/sdmx-twg/sdmx-ml)</mark></u>

<mark>-</mark> <u><mark>[SDMX-JSON specifications](https://github.com/sdmx-twg/sdmx-json)</mark></u>

<mark>-</mark> <u><mark>[SDMX-CSV specifications](https://github.com/sdmx-twg/sdmx-csv)</mark></u>

<mark>These resources provide details for querying structures, data and additional information (referential</mark>
<mark>metadata), as well as for error codes and more.</mark>
**Note, however, that only the syntax listed in this document is guaranteed to be supported.**

[For support questions, contact us at OECDdotStat@oecd.org.](mailto:OECDdotStat@oecd.org)

<mark>To report persistent issues or propose technical enhancements based on the SDMX standard please</mark>
<mark>[create tickets in https://gitlab.com/sis-cc/.stat-suite/dotstatsuite-core-sdmxri-nsi-ws/-/issues/.](https://gitlab.com/sis-cc/.stat-suite/dotstatsuite-core-sdmxri-nsi-ws/-/issues/)</mark>

8 ©OECD 2024

Unclassified - Non classifié

Last updated: 22 July 2024

## **API showcase**

<mark>[The OECD Data Explorer powered by the .Stat Suite](https://data-explorer.oecd.org/)</mark> <mark>is sourced by this API.</mark>

## **Upgrading your queries from the legacy OECD.Stat APIs**

<mark>[Please see here](https://gitlab.algobank.oecd.org/public-documentation/dotstat-migration/-/raw/main/OECD_Data_API_documentation-Upgrading_from_the_legacy_OECD.Stat_APIs.pdf)</mark> <mark>for documentation about how to upgrade your queries from the legacy OECD.Stat</mark>
<mark>APIs to the new OECD Data API.</mark>

9 ©OECD 2024

Unclassified - Non classifié

## API rate limiting

The OECD Data Explorer API is subject to rate limiting to protect the network, manage traffic efficiently, and ensure a responsive experience for all users.

API access is currently restricted to a maximum of **60 data downloads per hour.** Any requests exceeding this limit will be temporarily blocked. This restriction also applies to **CSV file downloads** from the [data-explorer.oecd.org](https://data-explorer.oecd.org) interface. Additionally, **traffic originating from VPNs or anonymized sources is not allowed**.

Please also note that API  requests using certain parameters are restricted, as they can impact overall system performance. The list of restricted parameters is available [here](https://www.oecd.org/en/data/insights/data-explainers/2026/03/Restricted-API-parameter.html)

## API best practices

The following suggestions are provided to support more efficient use of the OECD Data Explorer API, particularly for users who regularly retrieve the same datasets.

With limited exceptions, primarily for high-frequency economic indicators, most OECD datasets are updated infrequently (with revisions occurring primarily once or twice a year). As a result, API responses tend to remain relatively stable over time. Implementing efficient querying strategies can help minimise unnecessary repeated requests and reduce the need for large-scale downloads.

**1. Use the  contentconstraint query**

- This query provides a *ValidFrom*timestamp, indicating the last update time, and an Annotation with the total observation count.
- It also ensures you receive the latest version of the dataset, allowing you to verify if the dataset version has changed.

https://sdmx.oecd.org/public/rest/+contentconstraint/+AGENCY_ID+CR_A_+DATASET_ID

**2. Cache your results locally**

- If you or multiple users within the same organisation frequently fetch the same data, store results locally in a database or file.
- This avoids redundant requests, reduces server load, and prevents you from hitting API usage limits.

**3. Optimize query sizes**

- Run larger, consolidated queries whenever possible, rather than multiple smaller ones.
- For very large datasets (e.g., those with over 10 million records), consider breaking the queries into smaller slices for better manageability.

Thank you for following these best practices to ensure efficient and reliable use of the API.

