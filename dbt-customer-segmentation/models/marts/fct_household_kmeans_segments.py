def model(dbt, session):
    dbt.config(materialized="table")

    import pandas as pd
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_samples, silhouette_score

    feature_vectors = dbt.ref("fct_household_feature_vectors").df()
    centroids = dbt.ref("segment_centroids").df()

    cluster_count = int(dbt.config.get("python_cluster_count"))
    random_state = int(dbt.config.get("python_cluster_random_state"))

    if cluster_count < 2:
        raise ValueError("python_cluster_count must be at least 2 for silhouette scoring")

    feature_matrix = (
        feature_vectors
        .pivot(index="household_id", columns="feature_name", values="feature_value")
        .fillna(0.0)
        .sort_index()
    )

    if feature_matrix.shape[0] <= cluster_count:
        raise ValueError("python_cluster_count must be smaller than the number of households")

    model = KMeans(
        n_clusters=cluster_count,
        random_state=random_state,
        n_init=25,
        max_iter=1000,
    )
    labels = model.fit_predict(feature_matrix)
    silhouette_values = silhouette_samples(feature_matrix, labels)
    overall_silhouette = float(silhouette_score(feature_matrix, labels))

    cluster_centers = pd.DataFrame(
        model.cluster_centers_,
        columns=feature_matrix.columns,
    )
    cluster_centers["kmeans_cluster_id"] = cluster_centers.index

    cluster_centers_long = cluster_centers.melt(
        id_vars="kmeans_cluster_id",
        var_name="feature_name",
        value_name="cluster_feature_value",
    )

    cluster_to_segment = (
        cluster_centers_long
        .merge(
            centroids[["segment_id", "segment_name", "feature_name", "centroid_value", "feature_weight"]],
            on="feature_name",
            how="inner",
        )
        .assign(
            weighted_squared_distance=lambda df:
                df["feature_weight"] * (df["cluster_feature_value"] - df["centroid_value"]) ** 2
        )
        .groupby(["kmeans_cluster_id", "segment_id", "segment_name"], as_index=False)
        .agg(
            total_feature_weight=("feature_weight", "sum"),
            weighted_squared_distance=("weighted_squared_distance", "sum"),
        )
    )
    cluster_to_segment["cluster_segment_distance"] = (
        cluster_to_segment["weighted_squared_distance"] / cluster_to_segment["total_feature_weight"]
    ) ** 0.5
    cluster_to_segment["segment_rank"] = (
        cluster_to_segment
        .sort_values(["kmeans_cluster_id", "cluster_segment_distance", "segment_id"])
        .groupby("kmeans_cluster_id")
        .cumcount() + 1
    )
    cluster_to_segment = (
        cluster_to_segment[cluster_to_segment["segment_rank"] == 1]
        [["kmeans_cluster_id", "segment_id", "segment_name", "cluster_segment_distance"]]
    )

    assignments = pd.DataFrame({
        "household_id": feature_matrix.index,
        "kmeans_cluster_id": labels.astype(int),
        "distance_to_kmeans_center": model.transform(feature_matrix).min(axis=1),
        "kmeans_silhouette_score": silhouette_values,
        "overall_kmeans_silhouette_score": overall_silhouette,
    })

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
