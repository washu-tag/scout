from typing import List, Optional

from delta import DeltaTable
from pyspark.sql.types import StructType, StructField, StringType, BooleanType
from temporalio import activity

from .mappingentry import MappingEntry
from .sparkutils import (
    filter_df_for_update_inserts,
    merge_df_into_dt_on_column,
    extract_from_anticipated_column,
    create_table_from_df,
)

from pyspark.sql import functions as F, Column, DataFrame, Window, SparkSession
import uuid

mapping_schema = StructType(
    [
        StructField("scout_patient_id", StringType(), True),
        StructField("primary_report_identifier", StringType(), True),
        StructField("mpi", StringType(), True),
        StructField("epic_mrn", StringType(), True),
        StructField("consistent", BooleanType(), True),
    ]
)
history_schema = StructType(
    mapping_schema.fields
    + [StructField("previous_scout_patient_id", StringType(), True)]
)


def create_exact_match_condition(df1: DataFrame, df2: DataFrame) -> Column:
    return (df1["mpi"].eqNullSafe(df2["mpi"])) & (
        df1["epic_mrn"].eqNullSafe(df2["epic_mrn"])
    )


class _UnionFind:
    """Disjoint-set over hashable vertices, with path compression."""

    def __init__(self):
        self._parent: dict = {}

    def find(self, vertex):
        root = self._parent.setdefault(vertex, vertex)
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[vertex] != root:
            self._parent[vertex], vertex = root, self._parent[vertex]
        return root

    def union(self, left, right):
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root


def entry_vertices(entry: MappingEntry) -> List[tuple]:
    """The graph vertices an entry touches. Each mapping row is an edge joining an
    `mpi` vertex to an `epic_mrn` vertex; a patient is a connected component."""
    vertices = []
    if entry.mpi is not None:
        vertices.append(("mpi", entry.mpi))
    if entry.epic_mrn is not None:
        vertices.append(("epic_mrn", entry.epic_mrn))
    return vertices


def group_entries_by_component(entries: List[MappingEntry]) -> List[List[MappingEntry]]:
    """Partition entries into patient webs — the connected components of the graph
    described above.
    """
    union_find = _UnionFind()
    for entry in entries:
        vertices = entry_vertices(entry)
        for vertex in vertices[1:]:
            union_find.union(vertices[0], vertex)

    components: dict = {}
    for index, entry in enumerate(entries):
        vertices = entry_vertices(entry)
        # An entry carrying neither identifier links to nothing, so it is its own
        # component. Stage 4 input always has both, but existing rows may have neither.
        key = union_find.find(vertices[0]) if vertices else ("_row", index)
        components.setdefault(key, []).append(entry)
    return list(components.values())


class MappingTableExtractor:
    def __init__(self, spark: SparkSession, table_name: str):
        self.spark = spark
        self.table_name = table_name
        self.existing_mapping_df: Optional[DataFrame] = None
        self.deferred_reports_df: Optional[DataFrame] = None
        self.dataframes_to_unpersist: List[DataFrame] = []
        self.pinned_dataframes: List[DataFrame] = []

    def extract(self, batch_df: DataFrame):
        filtered_df = filter_df_for_update_inserts(
            batch_df, "primary_report_identifier"
        )
        if filtered_df is None:
            return
        processed_df = self.preprocess(filtered_df)
        df = self.process_stage_1(processed_df)
        df = self.process_stage_2(df)
        df = self.process_stage_3(df)
        self.process_stage_4(df)
        self.process_stage_5()
        self.postprocess()

    def cache(self, df: DataFrame) -> DataFrame:
        cached_df = df.cache()
        self.dataframes_to_unpersist.append(cached_df)
        return cached_df

    def pin(self, df: DataFrame) -> DataFrame:
        """Materialize a batch-sized frame and cut its lineage."""
        pinned_df = df.localCheckpoint(eager=True)
        self.pinned_dataframes.append(pinned_df)
        return pinned_df

    def unpin(self, pinned_df: DataFrame) -> None:
        """Release a pinned frame's blocks.

        `DataFrame.unpersist()` cannot: it goes to the cache manager, which only knows
        about `cache()`/`persist()` plans, while a local checkpoint's blocks belong to
        the RDD underneath the frame. Reach that RDD through the bare `LogicalRDD` plan
        `localCheckpoint` leaves behind. Only safe once nothing will read the frame
        again — the blocks are the checkpoint, so it cannot be recomputed after this.
        """
        plan = pinned_df._jdf.queryExecution().analyzed()
        plan_class = plan.getClass().getSimpleName()
        if plan_class != "LogicalRDD":
            activity.logger.warning(
                "Pinned frame's plan is a %s, not a LogicalRDD; leaving its checkpoint "
                "blocks for the JVM to reclaim",
                plan_class,
            )
            return
        plan.rdd().unpersist(False)

    def recache_existing_mapping(self):
        """Reread the mapping table, pinned to the version this read observes.

        Without `versionAsOf` the read is late-binding: a Delta write drops the cache of
        every frame whose plan references the table it wrote, and the recompute then
        resolves to post-merge state — so a frame's contents depend on when it happened
        to be recomputed. Pinning holds each generation of derived frames to the
        snapshot it was built against. Stages that must see an earlier stage's writes
        call this again afterwards, which is what advances the version.
        """
        if self.existing_mapping_df is not None:
            self.existing_mapping_df.unpersist()
        version = (
            DeltaTable.forName(self.spark, self.table_name).history(1).head()["version"]
        )
        self.existing_mapping_df = self.cache(
            self.spark.read.option("versionAsOf", version).table(self.table_name)
        )
        activity.logger.info(
            "Updated existing mapping table reread at version %d", version
        )

    def merge_to_dt(self, df: DataFrame):
        merge_df_into_dt_on_column(
            DeltaTable.forName(self.spark, self.table_name),
            df,
            "primary_report_identifier",
            False,
        )

    def preprocess(self, filtered_df: DataFrame) -> DataFrame:
        mapping_exists = self.spark.catalog.tableExists(self.table_name)
        df = filtered_df

        if mapping_exists:
            self.recache_existing_mapping()
            df = filtered_df.join(
                self.existing_mapping_df.select("primary_report_identifier"),
                on="primary_report_identifier",  # don't need to process again
                how="left_anti",
            )
        else:
            self.existing_mapping_df = self.spark.createDataFrame([], mapping_schema)
            (
                DeltaTable.createIfNotExists(self.spark)
                .tableName(self.table_name)
                .addColumns(mapping_schema)
                .property("delta.enableChangeDataFeed", "true")
                .execute()
            )

        df = df.withColumns(
            {
                "resolved_mpi": F.when(
                    F.col("version_id") == "2.7",
                    extract_from_anticipated_column("empi_mr", filtered_df),
                )
                .when(
                    F.col("version_id") == "2.4",
                    F.coalesce(
                        *[
                            extract_from_anticipated_column(
                                f"{authority}_ee", filtered_df
                            )
                            for authority in ["bjh", "bjwc", "slch"]
                        ]
                    ),
                )
                .otherwise(F.col("mpi")),
                "resolved_epic_mrn": F.when(
                    F.col("version_id") == "2.7",
                    F.coalesce(
                        *[
                            extract_from_anticipated_column(id, filtered_df)
                            for id in ["epic_mrn", "mbmc_mr"]
                        ]
                    ),
                ).otherwise(F.lit(None)),
            }
        ).select(
            "primary_report_identifier",
            F.col("resolved_mpi").alias("mpi"),
            F.col("resolved_epic_mrn").alias("epic_mrn"),
        )

        return self.cache(df)

    def process_stage_1(self, df: DataFrame) -> DataFrame:
        """
        Current DataFrame status: all incoming reports, other than reports we have already seen in a previous run.
        Goal: Find reports we can defer until the end to update in a bulk action defined by:
            1. Any incoming report with an exact match (both `mpi` and `epic_mrn`) in the mapping table already, OR
            2. Any incoming report with duplicated IDs among the rest of the incoming reports _except_ the first report.
        :param df: DataFrame of data to be processed
        """

        exact_match_condition = create_exact_match_condition(
            df, self.existing_mapping_df
        )

        exact_matches_df = df.join(
            self.existing_mapping_df, on=exact_match_condition, how="left_semi"
        )
        remaining_incoming_reports_df = df.join(
            self.existing_mapping_df, on=exact_match_condition, how="left_anti"
        )

        incoming_report_dupe_id_window = Window.partitionBy("mpi", "epic_mrn").orderBy(
            F.monotonically_increasing_id()
        )  # gather each combo of ids
        remaining_reports_ranked = self.cache(
            remaining_incoming_reports_df.withColumn(
                "_rank", F.row_number().over(incoming_report_dupe_id_window)
            )
        )

        unique_ids_incoming_reports = self.pin(
            remaining_reports_ranked.filter(F.col("_rank") == 1).drop("_rank")
        )
        duplicate_ids_incoming_reports = remaining_reports_ranked.filter(
            F.col("_rank") > 1
        ).drop("_rank")

        # Pinned: nothing reads this until stage 5
        self.deferred_reports_df = self.pin(
            exact_matches_df.unionByName(
                duplicate_ids_incoming_reports
            ).dropDuplicates()
        )

        activity.logger.info(
            "Stage 1 completed on mapping table derivation with %d deferred reports set aside",
            self.deferred_reports_df.count(),
        )

        return unique_ids_incoming_reports

    def process_stage_2(self, df: DataFrame) -> DataFrame:
        """
        Current DataFrame status:
            * Every report incoming has a unique ID combination
            * There are no exact matches of ID combinations between the incoming reports and the mapping table (or themselves)
        Goal: Create "consistent" rows for reports satisfying, with respect to `mpi` and `epic_mrn`:
            1. Are fully disjoint from the existing mapping table AND
            2. Are fully disjoint from the rest of the incoming reports
        :param df: DataFrame of data to be processed
        """

        mpi_matches_df = df.join(
            self.existing_mapping_df.select("mpi").withColumn(
                "_contains_match", F.lit(True)
            ),
            on="mpi",
            how="left",
        )

        epic_mrn_matches_df = df.join(
            self.existing_mapping_df.select("epic_mrn").withColumn(
                "_contains_match", F.lit(True)
            ),
            on="epic_mrn",
            how="left",
        )

        partial_match_with_indicator_df = self.pin(
            mpi_matches_df.unionByName(epic_mrn_matches_df)
            .groupBy("primary_report_identifier", "mpi", "epic_mrn")
            .agg(F.max("_contains_match").alias("_contains_match"))
        )

        partial_existing_mapping_match_df = self.pin(
            partial_match_with_indicator_df.filter(F.col("_contains_match")).drop(
                "_contains_match"
            )
        )

        no_existing_mapping_match_df = self.cache(
            partial_match_with_indicator_df.filter(
                F.col("_contains_match").isNull()
            ).drop("_contains_match")
        )

        mpi_counts = self.cache(
            no_existing_mapping_match_df.filter(F.col("mpi").isNotNull())
            .groupBy("mpi")
            .agg(F.count("*").alias("mpi_count"))
        )
        epic_mrn_counts = self.cache(
            no_existing_mapping_match_df.filter(F.col("epic_mrn").isNotNull())
            .groupBy("epic_mrn")
            .agg(F.count("*").alias("epic_mrn_count"))
        )

        def filter_no_existing_mapping_df(additional_filter: Column) -> DataFrame:
            return (
                no_existing_mapping_match_df.join(mpi_counts, on="mpi", how="left")
                .join(epic_mrn_counts, on="epic_mrn", how="left")
                .fillna(0, subset=["mpi_count", "epic_mrn_count"])
                .filter(additional_filter)
                .drop("mpi_count", "epic_mrn_count")
            )

        fully_disjoint_reports_df = self.cache(
            filter_no_existing_mapping_df(
                (F.col("mpi_count") <= 1) & (F.col("epic_mrn_count") <= 1)
            )
        )

        fully_disjoint_count = fully_disjoint_reports_df.count()
        activity.logger.info(
            "Calculated fully disjoint reports: %d", fully_disjoint_count
        )

        incoming_reports_with_links_df = self.pin(
            filter_no_existing_mapping_df(
                (F.col("mpi_count") > 1) | (F.col("epic_mrn_count") > 1)
            )
        )
        activity.logger.info(
            "Calculated stage 2 incoming reports with links: %d",
            incoming_reports_with_links_df.count(),
        )

        if fully_disjoint_count > 0:
            activity.logger.info(
                "Inserting mapping for %d fully disjoint reports",
                fully_disjoint_count,
            )
            fully_disjoint_reports_transformed_df = fully_disjoint_reports_df.select(
                F.expr("uuid()").alias("scout_patient_id"),
                "primary_report_identifier",
                "mpi",
                "epic_mrn",
                F.lit(True).alias("consistent"),
            )

            self.merge_to_dt(fully_disjoint_reports_transformed_df)
            activity.logger.info("Fully disjoint reports inserted")
            self.recache_existing_mapping()

        activity.logger.info(
            "Creating df as union of %d incoming links and %d partial existing matches",
            incoming_reports_with_links_df.count(),
            partial_existing_mapping_match_df.count(),
        )
        # Stage 2's output: stages 3 and 4 read it
        remaining_reports_df = self.pin(
            incoming_reports_with_links_df.unionByName(
                partial_existing_mapping_match_df
            ).dropDuplicates()
        )

        activity.logger.info(
            "Stage 2 remaining_reports_df final count: %d", remaining_reports_df.count()
        )

        activity.logger.info("Stage 2 completed on mapping table derivation")
        return remaining_reports_df

    def process_stage_3(self, df: DataFrame) -> DataFrame:
        """
        Current DataFrame status:
            * Every report incoming has a unique ID combination
            * There are no exact matches of ID combinations between the incoming reports and the mapping table (or themselves)
            * Every report in the incoming batch must have a partial ID match to another report, either in the batch or already in the mapping table
        Goal: Find rows with only one of `mpi` or `epic_mrn` specified and process:
            * For such reports with a partial match in the mapping table already, create a new row from our report, inheriting the `scout_patient_id` and `consistent` flag of the partial match
            * For such reports without a partial match, create a new consistent row in the mapping table
        Return remaining reports for further stages

        Both frames here are pinned, because both are read across a merge.

        :param df: DataFrame of data to be processed
        """

        exactly_one_id_specified_condition = (
            F.col("mpi").isNull() != F.col("epic_mrn").isNull()
        )
        reports_with_single_id_df = self.pin(
            df.filter(exactly_one_id_specified_condition)
        )

        def process_single_id_reports(field: str):
            return (
                reports_with_single_id_df.alias("incoming")
                .join(
                    self.existing_mapping_df.alias("existing"),
                    on=field,
                    how="inner",
                )
                .select(
                    "existing.scout_patient_id",
                    "incoming.primary_report_identifier",
                    "incoming.mpi",
                    "incoming.epic_mrn",
                    "existing.consistent",
                )
            )

        single_id_reports_with_mpi_match_df = process_single_id_reports("mpi")

        single_id_reports_with_epic_mrn_match_df = process_single_id_reports("epic_mrn")

        reports_with_partial_existing_match_df = self.pin(
            single_id_reports_with_mpi_match_df.unionByName(
                single_id_reports_with_epic_mrn_match_df
            ).dropDuplicates(["primary_report_identifier"])
        )

        partial_match_count = reports_with_partial_existing_match_df.count()
        activity.logger.info(
            "Stage 3 partial-match join resolved %d records", partial_match_count
        )
        if partial_match_count > 0:
            self.merge_to_dt(reports_with_partial_existing_match_df)
            activity.logger.info(
                "Merged %d mapping records with partial existing matches",
                partial_match_count,
            )

        reports_with_no_existing_match_df = self.pin(
            reports_with_single_id_df.join(
                reports_with_partial_existing_match_df.select(
                    "primary_report_identifier"
                ),
                on="primary_report_identifier",
                how="left_anti",
            ).select(
                F.expr("uuid()").alias("scout_patient_id"),
                "primary_report_identifier",
                "mpi",
                "epic_mrn",
                F.lit(True).alias("consistent"),
            )
        )
        no_match_count = reports_with_no_existing_match_df.count()
        activity.logger.info(
            "Stage 3 no-match anti-join resolved %d records", no_match_count
        )
        if no_match_count > 0:
            self.merge_to_dt(reports_with_no_existing_match_df)
            activity.logger.info(
                "Merged %d mapping records with no partial existing matches",
                no_match_count,
            )

        if partial_match_count > 0 or no_match_count > 0:
            self.recache_existing_mapping()

        activity.logger.info("Stage 3 completed on mapping table derivation")

        return (
            df.filter(~exactly_one_id_specified_condition)
            .withColumn("scout_patient_id", F.lit(None).cast(StringType()))
            .withColumn("consistent", F.lit(True))
        )

    def fetch_existing_matching_ids(self, mpis: set, epic_mrns: set) -> List:
        """One round of the closure below: every mapping row carrying one of these
        identifiers."""
        matches = []
        if mpis:
            matches.append(
                self.existing_mapping_df.join(
                    F.broadcast(
                        self.spark.createDataFrame([(v,) for v in mpis], "mpi string")
                    ),
                    on="mpi",
                    how="left_semi",
                )
            )
        if epic_mrns:
            matches.append(
                self.existing_mapping_df.join(
                    F.broadcast(
                        self.spark.createDataFrame(
                            [(v,) for v in epic_mrns], "epic_mrn string"
                        )
                    ),
                    on="epic_mrn",
                    how="left_semi",
                )
            )
        if not matches:
            return []
        combined_df = matches[0]
        for match_df in matches[1:]:
            combined_df = combined_df.unionByName(match_df)
        return combined_df.collect()

    def collect_existing_closure(
        self, seed_entries: List[MappingEntry]
    ) -> List[MappingEntry]:
        """Every mapping-table row transitively reachable from the batch's identifiers.

        Expands a frontier of not-yet-searched identifiers until it stops growing, one
        Spark job per round. Real components are shallow, so this settles in two rounds
        (the second reveals nothing new) unless identifiers genuinely chain — which only
        happens in an inconsistent web.
        """
        known_mpis = {e.mpi for e in seed_entries if e.mpi is not None}
        known_epic_mrns = {e.epic_mrn for e in seed_entries if e.epic_mrn is not None}
        frontier_mpis, frontier_epic_mrns = set(known_mpis), set(known_epic_mrns)

        found: dict = {}
        rounds = 0
        while frontier_mpis or frontier_epic_mrns:
            rows = self.fetch_existing_matching_ids(frontier_mpis, frontier_epic_mrns)
            rounds += 1
            frontier_mpis, frontier_epic_mrns = set(), set()
            for row in rows:
                if row["primary_report_identifier"] in found:
                    continue
                entry = MappingEntry.from_df_row(row, True)
                found[entry.primary_report_identifier] = entry
                if entry.mpi is not None and entry.mpi not in known_mpis:
                    known_mpis.add(entry.mpi)
                    frontier_mpis.add(entry.mpi)
                if entry.epic_mrn is not None and entry.epic_mrn not in known_epic_mrns:
                    known_epic_mrns.add(entry.epic_mrn)
                    frontier_epic_mrns.add(entry.epic_mrn)

        activity.logger.info(
            "Stage 4 closure settled after %d rounds, pulling in %d existing mappings",
            rounds,
            len(found),
        )
        return list(found.values())

    def process_stage_4(self, df: DataFrame):
        """
        Current DataFrame status:
            * Every report incoming has a unique ID combination
            * There are no exact matches of ID combinations between the incoming reports and the mapping table (or themselves)
            * Every report in the incoming batch must have a partial ID match to another report, either in the batch or already in the mapping table
            * Every report in the incoming batch has both a non-null `mpi` and `epic_mrn`
        Goal: Resolve the patient web each remaining report belongs to.

        The web is a connected component of the graph in which each mapping row is an
        edge joining an `mpi` vertex to an `epic_mrn` vertex. We close over the mapping
        table in a few Spark joins, then partition incoming and fetched rows into
        components in Python. See `docs/internal/patient_ids.md` for the semantics.
        :param df: DataFrame of data to be processed
        """
        rows = df.collect()
        activity.logger.info("Stage 4 input count: %d", len(rows))
        incoming_cases = [MappingEntry.from_df_row(row) for row in rows]
        if not incoming_cases:
            return

        existing_cases = self.collect_existing_closure(incoming_cases)
        patient_webs = group_entries_by_component(incoming_cases + existing_cases)
        activity.logger.info(
            "Resolving %d reports across %d patient webs",
            len(incoming_cases),
            len(patient_webs),
        )

        bulk_updates = []
        history_table_updates = []
        inconsistent_webs = 0
        for patient_web in patient_webs:
            known_mpis = {e.mpi for e in patient_web if e.mpi is not None}
            known_mrns = {e.epic_mrn for e in patient_web if e.epic_mrn is not None}
            unique_ids = list(
                dict.fromkeys(
                    entry.scout_patient_id
                    for entry in patient_web
                    if entry.scout_patient_id is not None
                )
            )
            generated_uuid = (
                unique_ids[0] if len(unique_ids) > 0 else str(uuid.uuid4())
            )  # take the only ID, generating a new one if none exist
            if (len(known_mpis) > 1) or (len(known_mrns) > 1):
                inconsistent_webs += 1
                for entry in patient_web:
                    if entry.consistent or entry.scout_patient_id != generated_uuid:
                        history_entry = entry.prepare_history_copy()
                        history_entry.scout_patient_id = generated_uuid
                        history_table_updates.append(history_entry)

                        entry.scout_patient_id = generated_uuid
                        entry.consistent = False
                        bulk_updates.append(entry)
            else:
                if len(unique_ids) < 2:
                    for entry in patient_web:
                        if (
                            not entry.existing_mapping
                        ):  # we only need to add new mapping entries
                            entry.scout_patient_id = generated_uuid
                            bulk_updates.append(entry)
                else:  # IDs are still consistent, but we must perform a patient merge
                    generated_uuid = unique_ids[0]
                    for entry in patient_web:
                        if entry.scout_patient_id != generated_uuid:
                            history_entry = entry.prepare_history_copy()
                            history_entry.scout_patient_id = generated_uuid
                            history_table_updates.append(history_entry)

                            entry.scout_patient_id = generated_uuid
                            bulk_updates.append(entry)

        if inconsistent_webs:
            activity.logger.info(
                "Inconsistent patient IDs found in %d of %d patient webs; every linked "
                "report mapping marked consistent=false and its prior value copied to "
                "%s_history",
                inconsistent_webs,
                len(patient_webs),
                self.table_name,
            )
        self.merge_to_dt(
            self.spark.createDataFrame(
                [mapping.to_dict() for mapping in bulk_updates],
                mapping_schema,
            )
        )
        if history_table_updates:
            create_table_from_df(
                self.spark.createDataFrame(
                    [
                        history_entry.to_dict_history()
                        for history_entry in history_table_updates
                    ],
                    history_schema,
                ),
                f"{self.table_name}_history",
            )

    def process_stage_5(self):
        """
        Goal: Process earlier deferred reports
        """

        deferred_report_count = self.deferred_reports_df.count()
        activity.logger.info(
            "Beginning stage 5: adding back in %d reports with guaranteed existing matches",
            deferred_report_count,
        )

        if deferred_report_count > 0:
            self.recache_existing_mapping()

            dupes_to_add_df = (
                self.deferred_reports_df.alias("incoming")
                .join(
                    self.existing_mapping_df.alias("existing"),
                    on=create_exact_match_condition(
                        self.deferred_reports_df, self.existing_mapping_df
                    ),
                    how="inner",
                )
                .select(
                    "existing.scout_patient_id",
                    "incoming.primary_report_identifier",  # take only the report ID from the incoming data
                    "existing.mpi",  # guaranteed a match, take everything from the match other than the report ID
                    "existing.epic_mrn",
                    "existing.consistent",
                )
                .dropDuplicates(["primary_report_identifier"])
            )

            self.merge_to_dt(dupes_to_add_df)

    def postprocess(self):
        for df in self.dataframes_to_unpersist:
            df.unpersist()
        for pinned_df in self.pinned_dataframes:
            self.unpin(pinned_df)
        activity.logger.info("Mapping table derivation complete")
