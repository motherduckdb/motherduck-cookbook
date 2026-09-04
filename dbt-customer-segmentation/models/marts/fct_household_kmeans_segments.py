def model(dbt, session):
    dbt.config(materialized="table")

    import pandas as pd
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_samples, silhouette_score

    def build_feature_matrix(feature_vectors):
        return (
            feature_vectors
            .pivot(index="household_id", columns="feature_name", values="feature_value")
            .fillna(0.0)
            .sort_index()
        )

    def validate_cluster_count(cluster_count, household_count):
        if cluster_count < 2:
            raise ValueError("python_cluster_count must be at least 2 for silhouette scoring")
        if household_count <= cluster_count:
            raise ValueError("python_cluster_count must be smaller than the number of households")

    def cluster_centers_long(kmeans, feature_names):
        cluster_centers = pd.DataFrame(kmeans.cluster_centers_, columns=feature_names)
        cluster_centers["kmeans_cluster_id"] = cluster_centers.index

        return cluster_centers.melt(
            id_vars="kmeans_cluster_id",
            var_name="feature_name",
            value_name="cluster_feature_value",
        )

    def align_clusters_to_segments(kmeans, feature_names, centroids):
        centroid_columns = [
            "segment_id",
            "segment_name",
            "feature_name",
            "centroid_value",
            "feature_weight",
        ]
        cluster_features = cluster_centers_long(kmeans, feature_names)
        scored_segments = cluster_features.merge(
            centroids[centroid_columns],
            on="feature_name",
            how="inner",
        )
        scored_segments["weighted_squared_distance"] = (
            scored_segments["feature_weight"]
            * (scored_segments["cluster_feature_value"] - scored_segments["centroid_value"]) ** 2
        )

        cluster_distances = (
            scored_segments
            .groupby(["kmeans_cluster_id", "segment_id", "segment_name"], as_index=False)
            .agg(
                total_feature_weight=("feature_weight", "sum"),
                weighted_squared_distance=("weighted_squared_distance", "sum"),
            )
        )
        cluster_distances["cluster_segment_distance"] = (
            cluster_distances["weighted_squared_distance"]
            / cluster_distances["total_feature_weight"]
        ) ** 0.5
        cluster_distances["segment_rank"] = (
            cluster_distances
            .sort_values(["kmeans_cluster_id", "cluster_segment_distance", "segment_id"])
            .groupby("kmeans_cluster_id")
            .cumcount() + 1
        )

        return cluster_distances.loc[
            cluster_distances["segment_rank"] == 1,
            ["kmeans_cluster_id", "segment_id", "segment_name", "cluster_segment_distance"],
        ]

    def build_assignments(feature_matrix, kmeans, labels):
        silhouette_values = silhouette_samples(feature_matrix, labels)
        overall_silhouette = float(silhouette_score(feature_matrix, labels))

        return pd.DataFrame({
            "household_id": feature_matrix.index,
            "kmeans_cluster_id": labels.astype(int),
            "distance_to_kmeans_center": kmeans.transform(feature_matrix).min(axis=1),
            "kmeans_silhouette_score": silhouette_values,
            "overall_kmeans_silhouette_score": overall_silhouette,
        })

    cluster_count = int(dbt.config.get("python_cluster_count"))
    random_state = int(dbt.config.get("python_cluster_random_state"))

    feature_vectors = dbt.ref("fct_household_feature_vectors").df()
    centroids = dbt.ref("segment_centroids").df()
    feature_matrix = build_feature_matrix(feature_vectors)
    validate_cluster_count(cluster_count, feature_matrix.shape[0])

    kmeans = KMeans(
        n_clusters=cluster_count,
        random_state=random_state,
        n_init=25,
        max_iter=1000,
    )
    labels = kmeans.fit_predict(feature_matrix)
    assignments = build_assignments(feature_matrix, kmeans, labels)
    cluster_to_segment = align_clusters_to_segments(kmeans, feature_matrix.columns, centroids)

    cluster_sizes = (
        assignments
        .groupby("kmeans_cluster_id", as_index=False)
        .agg(kmeans_cluster_households=("household_id", "count"))
    )

    return (
        assignments
        .merge(cluster_sizes, on="kmeans_cluster_id", how="left")
        .merge(cluster_to_segment, on="kmeans_cluster_id", how="left")
        .rename(
            columns={
                "segment_id": "aligned_segment_id",
                "segment_name": "aligned_segment_name",
            }
        )
        .sort_values("household_id")
    )
