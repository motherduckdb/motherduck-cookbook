select
    playbook.segment_id,
    playbook.segment_name
from {{ ref('segment_playbook') }} as playbook
left join {{ ref('fct_segment_feature_profiles') }} as profiles
    on playbook.segment_id = profiles.segment_id
group by 1, 2
having count(profiles.feature_name) = 0
