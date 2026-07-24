# See docs: "Architecture" — shared UI utilities re-exported for convenience.
# Internal modules can also import directly from the submodule files.

from yeaboi.ui.shared._animations import (  # noqa: F401
    BLACK_RGB,
    COLOR_RGB,
    FADE_IN_LEVELS,
    FADE_OUT_LEVELS,
    FRAME_TIME_30FPS,
    FRAME_TIME_60FPS,
    ease_out_cubic,
    fade_in,
    fade_out,
    lerp_color,
    loading_border_color,
    shimmer_style,
)
from yeaboi.ui.shared._ascii_font import render_ascii_text  # noqa: F401
from yeaboi.ui.shared._components import (  # noqa: F401
    ANALYSIS_THEME,
    PAD,
    PLANNING_THEME,
    SETTINGS_THEME,
    USAGE_THEME,
    Theme,
    analysis_title,
    build_action_buttons,
    build_popup,
    build_progress_dots,
    build_scrollbar,
    calc_viewport,
    center_label,
    planning_title,
    settings_title,
    usage_title,
)
from yeaboi.ui.shared._input import (  # noqa: F401
    disable_bracketed_paste,
    disable_mouse_tracking,
    enable_bracketed_paste,
    enable_mouse_tracking,
    read_key,
)
from yeaboi.ui.shared._scroll import (  # noqa: F401
    SCROLL_KEYS,
    WHEEL_STEP,
    apply_scroll,
    clamp_scroll,
    coalesce_scroll,
    coalesce_steps,
    max_scroll,
    publish_geometry,
)
