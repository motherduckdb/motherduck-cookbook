with ranked_distances as (
    select
        household_id,
        segment_id,
        segment_name,
        scored_feature_count,
        segment_distance,
        row_number() over (
            partition by household_id
            order by segment_distance, segment_id
        ) as distance_rank,
        lead(segment_id) over (
            partition by household_id
            order by segment_distance, segment_id
        ) as alternative_segment_id,
        lead(segment_name) over (
            partition by household_id
            order by segment_distance, segment_id
        ) as alternative_segment_name,
        lead(segment_distance) over (
            partition by household_id
            order by segment_distance, segment_id
        ) as alternative_segment_distance
    from {{ ref('fct_household_segment_distances') }}
),

nearest_segments as (
    select
        household_id,
        segment_id,
        segment_name,
        segment_distance,
        alternative_segment_id,
        alternative_segment_name,
        alternative_segment_distance,
        scored_feature_count
    from ranked_distances
    where distance_rank = 1
)

select
    nearest_segments.household_id,
    cast('{{ var("segmentation_reference_day") }}' as integer) as segmentation_reference_day,
    nearest_segments.segment_id,
    nearest_segments.segment_name,
    playbook.segment_label,
    playbook.segment_description,
    playbook.recommended_action,
    playbook.recommended_offer,
    nearest_segments.segment_distance,
    nearest_segments.alternative_segment_id,
    nearest_segments.alternative_segment_name,
    nearest_segments.alternative_segment_distance,
    greatest(
        0.0,
        least(
            1.0,
            case
                when nearest_segments.alternative_segment_distance is null
                    or nearest_segments.alternative_segment_distance = 0
                    then 1.0
                else
                    (
                        nearest_segments.alternative_segment_distance
                        - nearest_segments.segment_distance
                    )
                    / nearest_segments.alternative_segment_distance
            end
        )
    ) as segment_confidence,
    nearest_segments.scored_feature_count
from nearest_segments
left join {{ ref('segment_playbook') }} as playbook
    on nearest_segments.segment_id = playbook.segment_id
