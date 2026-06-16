from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from lifelines import CoxPHFitter, KaplanMeierFitter
from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

IBM_TELCO_DATASET_URL = (
    "https://raw.githubusercontent.com/IBM/telco-customer-churn-on-icp4d/master/data/"
    "Telco-Customer-Churn.csv"
)


@dataclass
class PreparedDataset:
    name: str
    frame: pd.DataFrame
    target_col: str
    metadata_cols: list[str]
    survival_frame: pd.DataFrame | None = None
    survival_duration_col: str | None = None
    survival_event_col: str | None = None
    current_frame: pd.DataFrame | None = None
    current_metadata_cols: list[str] | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark Python churn models and survival analysis for the dbt churn example. "
            "The default source uses IBM's official Telco dataset for realistic model training."
        )
    )
    parser.add_argument(
        "--source",
        choices=["ibm_telco", "dbt"],
        default="ibm_telco",
        help="Dataset source to use for training.",
    )
    parser.add_argument(
        "--database",
        default=None,
        help=(
            "DuckDB or MotherDuck database path. Required for --source dbt. "
            "If provided, the script also writes result tables back to the database."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts/python_models",
        help="Directory for metrics, plots, and model artifacts.",
    )
    parser.add_argument(
        "--write-schema",
        default="science",
        help="Schema name to use when writing outputs back to DuckDB or MotherDuck.",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=42,
        help="Random seed for reproducible splits and models.",
    )
    parser.add_argument(
        "--skip-survival",
        action="store_true",
        help="Skip survival analysis outputs.",
    )
    return parser.parse_args()


def load_ibm_telco_dataset() -> PreparedDataset:
    frame = pd.read_csv(IBM_TELCO_DATASET_URL)
    frame["TotalCharges"] = pd.to_numeric(frame["TotalCharges"], errors="coerce")
    frame["TotalCharges_missing"] = frame["TotalCharges"].isna().astype(int)
    # Tenure-zero customers have not accumulated charges yet, so keep them and make missingness explicit.
    frame["TotalCharges"] = frame["TotalCharges"].fillna(0.0)
    frame["Churn"] = frame["Churn"].map({"Yes": 1, "No": 0}).astype(int)
    frame["SeniorCitizen"] = frame["SeniorCitizen"].astype(int)

    return PreparedDataset(
        name="ibm_telco",
        frame=frame,
        target_col="Churn",
        metadata_cols=["customerID"],
        survival_frame=frame.copy(),
        survival_duration_col="tenure",
        survival_event_col="Churn",
    )


def load_dbt_training_dataset(database: str) -> PreparedDataset:
    connection = duckdb.connect(database)
    try:
        analytics_schema = connection.sql(
            """
            select table_schema
            from information_schema.tables
            where table_name = 'fct_customer_features_historical'
            order by case when table_schema like '%analytics' then 0 else 1 end, table_schema
            limit 1
            """
        ).fetchone()
        if analytics_schema is None:
            raise ValueError(
                "Could not find fct_customer_features_historical in the target database. "
                "Run `dbt build` first, or point --database at the correct DuckDB or MotherDuck database."
            )

        analytics_schema_name = analytics_schema[0]
        frame = connection.sql(
            f"""
            select
                features.customer_id,
                features.as_of_date,
                features.region_id,
                features.customer_name,
                features.marketing_opt_in,
                features.segment,
                features.is_active_member,
                features.membership_id,
                features.next_renewal_date,
                features.days_until_renewal,
                features.membership_age_days,
                features.prior_memberships,
                features.initial_plan_days,
                features.is_auto_renew,
                features.initial_payment_method,
                features.acquisition_channel,
                features.last_event_date,
                features.days_since_last_event,
                features.events_30d,
                features.events_60d,
                features.events_90d,
                features.events_120d,
                features.events_180d,
                features.spend_90d,
                features.complaints_60d,
                features.failed_payments_30d,
                features.avg_satisfaction_60d,
                labels.churned
            from {analytics_schema_name}.fct_customer_features_historical as features
            inner join {analytics_schema_name}.fct_customer_churn_labels as labels
                on features.customer_id = labels.customer_id
                and features.as_of_date = labels.as_of_date
                and features.segment = labels.segment
            where features.is_eligible_for_scoring
            """
        ).df()

        current_frame = connection.sql(
            f"""
            select
                customer_id,
                as_of_date,
                region_id,
                customer_name,
                marketing_opt_in,
                segment,
                is_active_member,
                membership_id,
                next_renewal_date,
                days_until_renewal,
                membership_age_days,
                prior_memberships,
                initial_plan_days,
                is_auto_renew,
                initial_payment_method,
                acquisition_channel,
                last_event_date,
                days_since_last_event,
                events_30d,
                events_60d,
                events_90d,
                events_120d,
                events_180d,
                spend_90d,
                complaints_60d,
                failed_payments_30d,
                avg_satisfaction_60d
            from {analytics_schema_name}.fct_customer_features_daily
            where is_eligible_for_scoring
            """
        ).df()

        survival_frame = connection.sql(
            f"""
            select
                membership_id,
                customer_id,
                duration_days,
                churned,
                prior_memberships,
                monthly_price,
                initial_plan_days,
                is_auto_renew,
                initial_payment_method,
                acquisition_channel
            from {analytics_schema_name}.fct_subscription_history
            """
        ).df()
    finally:
        connection.close()

    if len(frame) < 50 or frame["churned"].sum() < 10:
        raise ValueError(
            "The dbt training matrix is intentionally tiny in this example. Run the script with "
            "--source ibm_telco for meaningful benchmarking, or replace the sample seeds with a "
            "larger dataset before using --source dbt."
        )

    date_cols = ["as_of_date", "next_renewal_date", "last_event_date"]
    for column in date_cols:
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
        current_frame[column] = pd.to_datetime(current_frame[column], errors="coerce")
    frame["marketing_opt_in"] = frame["marketing_opt_in"].astype(bool)
    current_frame["marketing_opt_in"] = current_frame["marketing_opt_in"].astype(bool)

    return PreparedDataset(
        name="dbt",
        frame=frame,
        target_col="churned",
        metadata_cols=[
            "customer_id",
            "as_of_date",
            "customer_name",
            "membership_id",
            "next_renewal_date",
            "last_event_date",
        ],
        survival_frame=survival_frame,
        survival_duration_col="duration_days",
        survival_event_col="churned",
        current_frame=current_frame,
        current_metadata_cols=[
            "customer_id",
            "as_of_date",
            "customer_name",
            "membership_id",
            "next_renewal_date",
            "last_event_date",
        ],
    )


def choose_threshold(y_true: pd.Series, probabilities: np.ndarray) -> float:
    precision, recall, thresholds = precision_recall_curve(y_true, probabilities)
    if len(thresholds) == 0:
        return 0.5

    scores = 2 * precision[:-1] * recall[:-1] / np.clip(precision[:-1] + recall[:-1], 1e-9, None)
    best_index = int(np.nanargmax(scores))
    return float(thresholds[best_index])


def safe_metric(metric_fn, y_true: pd.Series, values: np.ndarray) -> float | None:
    if y_true.nunique() < 2:
        return None
    try:
        return float(metric_fn(y_true, values))
    except ValueError:
        return None


def evaluate_probabilities(
    y_true: pd.Series,
    probabilities: np.ndarray,
    threshold: float,
) -> dict[str, float | None]:
    predictions = (probabilities >= threshold).astype(int)
    return {
        "roc_auc": safe_metric(roc_auc_score, y_true, probabilities),
        "average_precision": safe_metric(average_precision_score, y_true, probabilities),
        "brier_score": safe_metric(brier_score_loss, y_true, probabilities),
        "precision_at_threshold": float(precision_score(y_true, predictions, zero_division=0)),
        "recall_at_threshold": float(recall_score(y_true, predictions, zero_division=0)),
        "f1_at_threshold": float(f1_score(y_true, predictions, zero_division=0)),
    }


def split_frame(
    frame: pd.DataFrame,
    target_col: str,
    metadata_cols: list[str],
    random_state: int,
    time_split_col: str | None = None,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.Series,
    pd.Series,
    pd.Series,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    metadata = frame[metadata_cols].copy()
    y = frame[target_col].copy()
    X = frame.drop(columns=metadata_cols + [target_col]).copy()

    if time_split_col is not None:
        snapshot_dates = sorted(pd.Series(metadata[time_split_col].dropna().unique()).tolist())
        if len(snapshot_dates) < 3:
            raise ValueError(
                f"Need at least 3 distinct {time_split_col} values for a time-based train/validation/test split."
            )

        test_date = snapshot_dates[-1]
        validation_date = snapshot_dates[-2]
        train_dates = snapshot_dates[:-2]

        train_mask = metadata[time_split_col].isin(train_dates)
        validation_mask = metadata[time_split_col] == validation_date
        test_mask = metadata[time_split_col] == test_date

        X_train = X.loc[train_mask].reset_index(drop=True)
        X_val = X.loc[validation_mask].reset_index(drop=True)
        X_test = X.loc[test_mask].reset_index(drop=True)
        y_train = y.loc[train_mask].reset_index(drop=True)
        y_val = y.loc[validation_mask].reset_index(drop=True)
        y_test = y.loc[test_mask].reset_index(drop=True)
        metadata_train = metadata.loc[train_mask].reset_index(drop=True)
        metadata_val = metadata.loc[validation_mask].reset_index(drop=True)
        metadata_test = metadata.loc[test_mask].reset_index(drop=True)

        if min(len(X_train), len(X_val), len(X_test)) == 0:
            raise ValueError("Time-based split produced an empty train, validation, or test partition.")

        return (
            X_train,
            X_val,
            X_test,
            y_train,
            y_val,
            y_test,
            metadata_train,
            metadata_val,
            metadata_test,
        )

    X_train_val, X_test, y_train_val, y_test, metadata_train_val, metadata_test = train_test_split(
        X,
        y,
        metadata,
        test_size=0.2,
        random_state=random_state,
        stratify=y,
    )
    X_train, X_val, y_train, y_val, metadata_train, metadata_val = train_test_split(
        X_train_val,
        y_train_val,
        metadata_train_val,
        test_size=0.25,
        random_state=random_state,
        stratify=y_train_val,
    )
    return (
        X_train,
        X_val,
        X_test,
        y_train,
        y_val,
        y_test,
        metadata_train.reset_index(drop=True),
        metadata_val.reset_index(drop=True),
        metadata_test.reset_index(drop=True),
    )


def build_estimators(
    numeric_cols: list[str],
    categorical_cols: list[str],
    random_state: int,
) -> dict[str, Pipeline]:
    numeric_pipeline = Pipeline(
        steps=[
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]
    )
    categorical_pipeline = Pipeline(
        steps=[
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("encode", OneHotEncoder(handle_unknown="ignore")),
        ]
    )
    one_hot_preprocessor = ColumnTransformer(
        transformers=[
            ("num", numeric_pipeline, numeric_cols),
            ("cat", categorical_pipeline, categorical_cols),
        ]
    )

    ordinal_categorical_pipeline = Pipeline(
        steps=[
            ("impute", SimpleImputer(strategy="most_frequent")),
            (
                "encode",
                OrdinalEncoder(
                    handle_unknown="use_encoded_value",
                    unknown_value=np.nan,
                    encoded_missing_value=np.nan,
                ),
            ),
        ]
    )
    ordinal_preprocessor = ColumnTransformer(
        transformers=[
            ("num", SimpleImputer(strategy="median"), numeric_cols),
            ("cat", ordinal_categorical_pipeline, categorical_cols),
        ],
        verbose_feature_names_out=False,
    )

    hist_categorical_indexes = list(range(len(numeric_cols), len(numeric_cols) + len(categorical_cols)))

    return {
        "logistic_regression": Pipeline(
            steps=[
                ("preprocess", one_hot_preprocessor),
                (
                    "classifier",
                    LogisticRegression(
                        max_iter=2000,
                        class_weight="balanced",
                        solver="liblinear",
                        random_state=random_state,
                    ),
                ),
            ]
        ),
        "random_forest": Pipeline(
            steps=[
                ("preprocess", one_hot_preprocessor),
                (
                    "classifier",
                    RandomForestClassifier(
                        n_estimators=400,
                        min_samples_leaf=5,
                        class_weight="balanced_subsample",
                        random_state=random_state,
                        n_jobs=-1,
                    ),
                ),
            ]
        ),
        "hist_gradient_boosting": Pipeline(
            steps=[
                ("preprocess", ordinal_preprocessor),
                (
                    "classifier",
                    HistGradientBoostingClassifier(
                        categorical_features=hist_categorical_indexes or None,
                        class_weight="balanced",
                        learning_rate=0.05,
                        max_depth=6,
                        max_iter=250,
                        min_samples_leaf=20,
                        random_state=random_state,
                    ),
                ),
            ]
        ),
    }


def run_model_benchmark(
    dataset: PreparedDataset,
    output_dir: Path,
    random_state: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any], Any]:
    (
        X_train,
        X_val,
        X_test,
        y_train,
        y_val,
        y_test,
        _metadata_train,
        _metadata_val,
        metadata_test,
    ) = split_frame(
        frame=dataset.frame,
        target_col=dataset.target_col,
        metadata_cols=dataset.metadata_cols,
        random_state=random_state,
        time_split_col="as_of_date" if dataset.name == "dbt" and "as_of_date" in dataset.metadata_cols else None,
    )

    numeric_cols = X_train.select_dtypes(include=["number", "bool"]).columns.tolist()
    categorical_cols = [column for column in X_train.columns if column not in numeric_cols]
    estimators = build_estimators(
        numeric_cols=numeric_cols,
        categorical_cols=categorical_cols,
        random_state=random_state,
    )

    metric_rows: list[dict[str, Any]] = []
    validation_curves: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    validation_pr_curves: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    fitted_estimators: dict[str, Any] = {}
    best_model_name: str | None = None
    best_model_score = -np.inf
    best_threshold = 0.5

    for name, estimator in estimators.items():
        fitted = clone(estimator)
        fitted.fit(X_train, y_train)
        probabilities = fitted.predict_proba(X_val)[:, 1]
        threshold = choose_threshold(y_val, probabilities)
        metrics = evaluate_probabilities(y_val, probabilities, threshold)
        metric_rows.append({"model_name": name, "evaluation_split": "validation", "threshold": threshold, **metrics})
        validation_curves[name] = roc_curve(y_val, probabilities)[:2]
        precision, recall, _ = precision_recall_curve(y_val, probabilities)
        validation_pr_curves[name] = (precision, recall)
        fitted_estimators[name] = fitted

        selection_score = metrics["average_precision"] if metrics["average_precision"] is not None else -np.inf
        if selection_score > best_model_score:
            best_model_score = selection_score
            best_model_name = name
            best_threshold = threshold

    assert best_model_name is not None
    best_validation_estimator = fitted_estimators[best_model_name]
    calibration_folds = max(2, min(5, int(y_train.value_counts().min())))
    calibrated_estimator = CalibratedClassifierCV(
        estimator=clone(estimators[best_model_name]),
        method="sigmoid",
        cv=calibration_folds,
    )
    calibrated_estimator.fit(pd.concat([X_train, X_val]), pd.concat([y_train, y_val]))

    calibrated_probabilities = calibrated_estimator.predict_proba(X_test)[:, 1]
    test_metrics = evaluate_probabilities(y_test, calibrated_probabilities, best_threshold)
    metric_rows.append(
        {
            "model_name": f"{best_model_name}_calibrated",
            "evaluation_split": "test",
            "threshold": best_threshold,
            **test_metrics,
        }
    )

    predictions = metadata_test.copy()
    predictions["actual_churn"] = y_test.reset_index(drop=True).to_numpy()
    predictions["predicted_probability"] = calibrated_probabilities
    predictions["predicted_churn"] = (calibrated_probabilities >= best_threshold).astype(int)
    predictions["model_name"] = best_model_name
    predictions["calibrated"] = True

    feature_importance = extract_feature_importance(
        best_validation_estimator,
        best_model_name,
        X_val,
        y_val,
        random_state,
    )
    feature_importance.insert(0, "model_name", best_model_name)

    summary = {
        "dataset": dataset.name,
        "row_count": int(len(dataset.frame)),
        "positive_rate": float(dataset.frame[dataset.target_col].mean()),
        "selected_model": best_model_name,
        "selected_threshold": best_threshold,
        "categorical_features": categorical_cols,
        "numeric_features": numeric_cols,
    }

    current_scores = build_current_scores(
        dataset=dataset,
        calibrated_estimator=calibrated_estimator,
        threshold=best_threshold,
    )

    plot_validation_curves(validation_curves, validation_pr_curves, output_dir)
    plot_test_distribution(calibrated_probabilities, output_dir)
    return (
        pd.DataFrame(metric_rows),
        predictions,
        feature_importance,
        current_scores,
        summary,
        calibrated_estimator,
    )


def build_current_scores(
    dataset: PreparedDataset,
    calibrated_estimator: Any,
    threshold: float,
) -> pd.DataFrame:
    if dataset.current_frame is None or not dataset.current_metadata_cols:
        return pd.DataFrame()

    current_metadata = dataset.current_frame[dataset.current_metadata_cols].copy().reset_index(drop=True)
    current_features = dataset.current_frame.drop(columns=dataset.current_metadata_cols).copy()
    current_probabilities = calibrated_estimator.predict_proba(current_features)[:, 1]

    current_scores = current_metadata.copy()
    current_scores["predicted_probability"] = current_probabilities
    current_scores["predicted_churn"] = (current_probabilities >= threshold).astype(int)
    current_scores["threshold"] = threshold
    return current_scores


def extract_feature_importance(
    fitted_estimator: Any,
    model_name: str,
    X_reference: pd.DataFrame,
    y_reference: pd.Series,
    random_state: int,
) -> pd.DataFrame:
    preprocess = fitted_estimator.named_steps["preprocess"]
    classifier = fitted_estimator.named_steps["classifier"]
    feature_names = preprocess.get_feature_names_out()

    if model_name == "logistic_regression":
        importance = np.abs(classifier.coef_[0])
    elif hasattr(classifier, "feature_importances_"):
        importance = classifier.feature_importances_
    else:
        result = permutation_importance(
            fitted_estimator,
            X_reference,
            y_reference,
            n_repeats=20,
            random_state=random_state,
            scoring="average_precision",
        )
        importance = result.importances_mean

    feature_frame = pd.DataFrame(
        {
            "feature_name": feature_names,
            "importance": importance,
        }
    )
    return feature_frame.sort_values("importance", ascending=False).head(20).reset_index(drop=True)


def plot_validation_curves(
    roc_curves: dict[str, tuple[np.ndarray, np.ndarray]],
    pr_curves: dict[str, tuple[np.ndarray, np.ndarray]],
    output_dir: Path,
) -> None:
    figure, axes = plt.subplots(1, 2, figsize=(12, 5))

    for model_name, (false_positive_rate, true_positive_rate) in roc_curves.items():
        axes[0].plot(false_positive_rate, true_positive_rate, label=model_name)
    axes[0].plot([0, 1], [0, 1], linestyle="--", color="grey", linewidth=1)
    axes[0].set_title("Validation ROC Curves")
    axes[0].set_xlabel("False positive rate")
    axes[0].set_ylabel("True positive rate")
    axes[0].legend()

    for model_name, (precision, recall) in pr_curves.items():
        axes[1].plot(recall, precision, label=model_name)
    axes[1].set_title("Validation Precision-Recall Curves")
    axes[1].set_xlabel("Recall")
    axes[1].set_ylabel("Precision")
    axes[1].legend()

    figure.tight_layout()
    figure.savefig(output_dir / "validation_model_curves.png", dpi=200)
    plt.close(figure)


def plot_test_distribution(probabilities: np.ndarray, output_dir: Path) -> None:
    figure, axis = plt.subplots(figsize=(7, 4))
    axis.hist(probabilities, bins=20, color="#3b82f6", edgecolor="white")
    axis.set_title("Calibrated Test Probability Distribution")
    axis.set_xlabel("Predicted churn probability")
    axis.set_ylabel("Customer count")
    figure.tight_layout()
    figure.savefig(output_dir / "test_probability_distribution.png", dpi=200)
    plt.close(figure)


def run_survival_analysis(dataset: PreparedDataset, output_dir: Path) -> pd.DataFrame:
    if dataset.survival_frame is None or dataset.survival_duration_col is None or dataset.survival_event_col is None:
        return pd.DataFrame()

    survival_frame = dataset.survival_frame.copy()
    duration_col = dataset.survival_duration_col
    event_col = dataset.survival_event_col

    kmf = KaplanMeierFitter()
    kmf.fit(survival_frame[duration_col], survival_frame[event_col])

    km_rows = [
        {
            "segment_name": "overall",
            "segment_value": "all",
            "median_survival_time": float(kmf.median_survival_time_),
            "survival_probability_at_12": float(
                kmf.survival_function_at_times([12]).iloc[0]
                if dataset.name == "ibm_telco"
                else kmf.survival_function_at_times([30]).iloc[0]
            ),
        }
    ]

    plot_kaplan_meier_groups(survival_frame, duration_col, event_col, output_dir, km_rows, dataset.name)
    cox_summary = fit_cox_model(dataset, survival_frame, duration_col, event_col)
    return pd.DataFrame(km_rows + cox_summary)


def plot_kaplan_meier_groups(
    survival_frame: pd.DataFrame,
    duration_col: str,
    event_col: str,
    output_dir: Path,
    km_rows: list[dict[str, Any]],
    dataset_name: str,
) -> None:
    if dataset_name == "ibm_telco":
        grouping_col = "Contract"
        output_name = "kaplan_meier_contract.png"
    else:
        grouping_col = "initial_payment_method"
        output_name = "kaplan_meier_payment_method.png"

    figure, axis = plt.subplots(figsize=(8, 5))
    for value in sorted(survival_frame[grouping_col].dropna().astype(str).unique()):
        mask = survival_frame[grouping_col].astype(str) == value
        kmf = KaplanMeierFitter(label=value)
        kmf.fit(survival_frame.loc[mask, duration_col], survival_frame.loc[mask, event_col])
        kmf.plot(ax=axis, ci_show=False)
        km_rows.append(
            {
                "segment_name": grouping_col,
                "segment_value": value,
                "median_survival_time": float(kmf.median_survival_time_),
                "survival_probability_at_12": float(
                    kmf.survival_function_at_times([12]).iloc[0]
                    if dataset_name == "ibm_telco"
                    else kmf.survival_function_at_times([30]).iloc[0]
                ),
            }
        )

    axis.set_title(f"Kaplan-Meier by {grouping_col}")
    axis.set_xlabel("Time")
    axis.set_ylabel("Survival probability")
    figure.tight_layout()
    figure.savefig(output_dir / output_name, dpi=200)
    plt.close(figure)


def fit_cox_model(
    dataset: PreparedDataset,
    survival_frame: pd.DataFrame,
    duration_col: str,
    event_col: str,
) -> list[dict[str, Any]]:
    if dataset.name == "ibm_telco":
        modeling_frame = survival_frame.drop(columns=["customerID"]).copy()
        categorical_cols = modeling_frame.select_dtypes(include=["object"]).columns.tolist()
        modeling_frame = pd.get_dummies(modeling_frame, columns=categorical_cols, drop_first=True)
    else:
        modeling_frame = survival_frame.copy()
        categorical_cols = ["initial_payment_method", "acquisition_channel"]
        modeling_frame = pd.get_dummies(modeling_frame, columns=categorical_cols, drop_first=True)
        modeling_frame = modeling_frame.drop(columns=["membership_id", "customer_id"])

    cox = CoxPHFitter(penalizer=0.1)
    cox.fit(modeling_frame, duration_col=duration_col, event_col=event_col)

    top_effects = (
        cox.summary.reset_index()
        .rename(columns={"covariate": "feature_name", "exp(coef)": "hazard_ratio", "p": "p_value"})
        .sort_values("hazard_ratio", ascending=False)
        .head(10)
    )

    rows = [
        {
            "segment_name": "cox_model",
            "segment_value": row.feature_name,
            "median_survival_time": None,
            "survival_probability_at_12": None,
            "hazard_ratio": float(row.hazard_ratio),
            "p_value": float(row.p_value),
            "concordance_index": float(cox.concordance_index_),
        }
        for row in top_effects.itertuples()
    ]
    return rows


def write_outputs(
    output_dir: Path,
    metrics: pd.DataFrame,
    predictions: pd.DataFrame,
    feature_importance: pd.DataFrame,
    current_scores: pd.DataFrame,
    survival_summary: pd.DataFrame,
    summary: dict[str, Any],
    calibrated_estimator: Any,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    metrics.to_csv(output_dir / "model_metrics.csv", index=False)
    predictions.to_csv(output_dir / "test_predictions.csv", index=False)
    feature_importance.to_csv(output_dir / "top_feature_importance.csv", index=False)
    if not current_scores.empty:
        current_scores.to_csv(output_dir / "current_scores.csv", index=False)
    if not survival_summary.empty:
        survival_summary.to_csv(output_dir / "survival_summary.csv", index=False)
    with (output_dir / "run_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
    joblib.dump(calibrated_estimator, output_dir / "best_model.joblib")


def write_outputs_to_database(
    database: str,
    schema: str,
    metrics: pd.DataFrame,
    predictions: pd.DataFrame,
    feature_importance: pd.DataFrame,
    current_scores: pd.DataFrame,
    survival_summary: pd.DataFrame,
) -> None:
    connection = duckdb.connect(database)
    try:
        connection.execute(f"create schema if not exists {schema}")
        connection.register("metrics_df", metrics)
        connection.register("predictions_df", predictions)
        connection.register("feature_importance_df", feature_importance)
        connection.execute(
            f"create or replace table {schema}.python_churn_model_metrics as select * from metrics_df"
        )
        connection.execute(
            f"create or replace table {schema}.python_churn_test_predictions as select * from predictions_df"
        )
        connection.execute(
            f"create or replace table {schema}.python_churn_feature_importance as select * from feature_importance_df"
        )
        if not current_scores.empty:
            connection.register("current_scores_df", current_scores)
            connection.execute(
                f"create or replace table {schema}.python_churn_current_scores as select * from current_scores_df"
            )
        if not survival_summary.empty:
            connection.register("survival_summary_df", survival_summary)
            connection.execute(
                f"create or replace table {schema}.python_churn_survival_summary as select * from survival_summary_df"
            )
    finally:
        connection.close()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.source == "dbt":
        if not args.database:
            raise ValueError("--database is required when --source dbt is used.")
        dataset = load_dbt_training_dataset(args.database)
    else:
        dataset = load_ibm_telco_dataset()

    metrics, predictions, feature_importance, current_scores, summary, calibrated_estimator = run_model_benchmark(
        dataset=dataset,
        output_dir=output_dir,
        random_state=args.random_state,
    )
    survival_summary = pd.DataFrame()
    if not args.skip_survival:
        survival_summary = run_survival_analysis(dataset, output_dir)

    write_outputs(
        output_dir=output_dir,
        metrics=metrics,
        predictions=predictions,
        feature_importance=feature_importance,
        current_scores=current_scores,
        survival_summary=survival_summary,
        summary=summary,
        calibrated_estimator=calibrated_estimator,
    )
    if args.database:
        write_outputs_to_database(
            database=args.database,
            schema=args.write_schema,
            metrics=metrics,
            predictions=predictions,
            feature_importance=feature_importance,
            current_scores=current_scores,
            survival_summary=survival_summary,
        )

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
