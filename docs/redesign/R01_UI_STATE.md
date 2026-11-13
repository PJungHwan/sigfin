# R01 UI 상태·실행 키 목록

기준: `4266787` / 2026-11-12. AST로 조사한 정적 키 표현식이다. 실제 세션 값·API 키·사용자 원장/보고서는 수집하지 않았다. 동적 키는 표현식을 기록하며 모든 실행 시점의 실제 키를 열거했다고 주장하지 않는다.

페이지 이동에 보존할 애플리케이션 상태와 위젯/실행 이벤트를 구분해야 한다. 버튼 이벤트·업로더 값을 무조건 복원하지 않는다. 보호 규칙은 [기준선](R01_BASELINE.md)을 따른다.

## app.py

[소스](../../app.py)

함수: 진입점 조립.

- Session State 참조: 없음.
- 위젯/컨테이너 key 표현식: `'analyze_ai'`, `'macro_cpi'`, `'macro_dgs10'`, `'macro_fed'`, `'macro_nfp'`, `'macro_unrate'`, `'market_chart'`, `'period'`, `'refresh_market'`, `'show_bollinger'`, `'show_ema'`, `'show_macd'`, `'show_rsi'`, `'show_sma'`, `'show_strategy_signals'`, `'show_volume_sma'`, `'symbol'`, `'timeframe'`.

## ui/ai_analysis_view.py

[소스](../../ui/ai_analysis_view.py)

함수: `render_ai_controls`, `_usage`, `_display`, `render_ai_analysis`.

- Session State 참조: `'_ai_approval_signature'`, `'_ai_ledger'`, `'_ai_result'`, `'_ai_signature'`, `'_macro_results'`, `'ai_cost_approved'`.
- 위젯/컨테이너 key 표현식: `'ai_cost_approved'`, `'ai_cost_limit'`, `'ai_mode'`, `'ai_output_limit'`, `'ai_request_limit'`, `'ai_retries'`.

## ui/backtest_charts.py

[소스](../../ui/backtest_charts.py)

함수: `equity_chart`, `trade_pnl_chart`, `comparison_equity_chart`.

- Session State 참조: 없음.
- 위젯/컨테이너 key 표현식: 없음.

## ui/backtest_view.py

[소스](../../ui/backtest_view.py)

함수: `risk_summary`, `render_backtest_controls`, `render_risk_controls`, `result_fingerprint`, `clear_backtest_result`, `execute_or_get_result`, `_format`, `render_risk_comparison`, `render_backtest_result`.

- Session State 참조: `'_backtest_key'`, `'_backtest_result'`, `'_risk_comparison'`.
- 위젯/컨테이너 key 표현식: `'backtest_equity'`, `'backtest_trade_pnl'`, `'bt_capital'`, `'bt_fee'`, `'bt_participation'`, `'bt_slippage'`, `'compare_risk'`, `'risk_comparison_equity'`, `'run_backtest'`, `f'risk_{key}_enabled'`, `f'risk_{key}_rate'`.

## ui/charts.py

[소스](../../ui/charts.py)

함수: `create_candlestick_chart`, `add_line`.

- Session State 참조: 없음.
- 위젯/컨테이너 key 표현식: 없음.

## ui/components.py

[소스](../../ui/components.py)

함수: `metric_card`, `rsi_metric_card`, `macd_metric_card`, `agent_status`.

- Session State 참조: 없음.
- 위젯/컨테이너 key 표현식: 없음.

## ui/crypto_macro_charts.py

[소스](../../ui/crypto_macro_charts.py)

함수: `_style`, `normalized_trend_chart`, `correlation_scatter_chart`, `lag_correlation_chart`.

- Session State 참조: 없음.
- 위젯/컨테이너 key 표현식: 없음.

## ui/crypto_macro_view.py

[소스](../../ui/crypto_macro_view.py)

함수: `render_crypto_macro_controls`, `_table`, `_render_result`, `render_crypto_macro_data`.

- Session State 참조: `'_cm_result'`, `'_cm_signature'`.
- 위젯/컨테이너 key 표현식: `'cm_dgs_aggregation'`, `'cm_lags'`, `'cm_mode'`, `'cm_normalized'`, `'cm_partial'`, `'cm_period'`, `'cm_scatter'`, `'cm_series'`, `'cm_timeframe'`, `'load_crypto_macro'`, `f'cm_transform_{series}'`.

## ui/debate_view.py

[소스](../../ui/debate_view.py)

함수: `render_debate`.

- Session State 참조: 없음.
- 위젯/컨테이너 key 표현식: 없음.

## ui/experiment_view.py

[소스](../../ui/experiment_view.py)

함수: `current_input_key`, `experiment_equity_chart`, `display_result_verification`, `display_experiment`, `render_experiments`.

- Session State 참조: `'_experiment_current'`, `'_experiment_replay'`, `'_experiment_replay_reference'`, `'_experiment_replay_reference_source'`, `'_experiment_replay_upload_digest'`, `'_experiment_strategies'`, `name`.
- 위젯/컨테이너 key 표현식: `'exp_add_strategy'`, `'exp_remove_selection'`, `'exp_remove_strategy'`, `'exp_replay'`, `'exp_run'`, `'exp_split_enabled'`, `'exp_split_percent'`, `'exp_upload'`, `'experiment_replay_result'`, `f'exp_json_{suffix}'`, `f'exp_md_{suffix}'`, `f'exp_result_segment_{suffix}'`, `f'experiment_equity_{suffix}'`.

## ui/macro_charts.py

[소스](../../ui/macro_charts.py)

함수: `macro_series_chart`.

- Session State 참조: 없음.
- 위젯/컨테이너 key 표현식: 없음.

## ui/macro_view.py

[소스](../../ui/macro_view.py)

함수: `render_macro_controls`, `render_macro_data`.

- Session State 참조: `'_macro_results'`, `'_macro_signature'`.
- 위젯/컨테이너 key 표현식: `'load_macro'`, `'macro_cpi_view'`, `'macro_period'`, `f'macro_chart_{series_id}'`.

## ui/portfolio_risk_view.py

[소스](../../ui/portfolio_risk_view.py)

함수: `render_portfolio_risk`.

- Session State 참조: `'_portfolio_account'`, `'_portfolio_history'`, `'_portfolio_risk'`, `'_portfolio_risk_logs'`, `'_portfolio_risk_signature'`.
- 위젯/컨테이너 key 표현식: `'pr_allowed'`, `'pr_btc_drop'`, `'pr_concentration'`, `'pr_eth_drop'`, `'pr_fee'`, `'pr_slippage'`, `'run_portfolio_risk'`.

## ui/portfolio_view.py

[소스](../../ui/portfolio_view.py)

함수: `portfolio_now`, `accept_account`, `render_portfolio`.

- Session State 참조: `'_portfolio_account'`, `'_portfolio_autoloaded'`, `'_portfolio_frames'`, `'_portfolio_nonce'`, `'_portfolio_price_context'`, `'_portfolio_revision'`, `'_portfolio_valuation'`.
- 위젯/컨테이너 key 표현식: `'pf_capital'`, `'pf_create'`, `'pf_fee'`, `'pf_load_prices'`, `'pf_quantity'`, `'pf_reset_confirm'`, `'pf_side'`, `'pf_slippage'`, `'pf_symbol'`, `'pf_trade'`, `'pf_trade_confirm'`, `'pf_trade_form'`.

## ui/research_view.py

[소스](../../ui/research_view.py)

함수: `research_now`, `render_research_raw_data`, `display_research_report`, `current_research_context`, `render_research`.

- Session State 참조: `'_ai_ledger'`, `'_backtest_key'`, `'_backtest_result'`, `'_macro_results'`, `'_portfolio_account'`, `'_portfolio_frames'`, `'_portfolio_history'`, `'_portfolio_risk'`, `'_portfolio_risk_signature'`, `'_research_execution'`, `'_research_signature'`, `'_research_snapshot'`, `'_restored_analysis_generation'`, `'_workflow_execution'`, `'ai_cost_limit'`, `'ai_output_limit'`, `'ai_request_limit'`, `'research_cost_approved'`, `'research_memo'`.
- 위젯/컨테이너 key 표현식: `'research_cost_approved'`, `'research_json'`, `'research_md'`, `'research_memo'`, `'research_mode'`, `'run_research'`, `'save_research'`, `f'research_raw_{scope}_{saved.record_id}_{generation}'`.

## ui/risk_manager_view.py

[소스](../../ui/risk_manager_view.py)

함수: `render_risk_manager_controls`, `display_number`, `render_risk_manager`.

- Session State 참조: `'bt_capital'`, `'bt_fee'`, `'bt_slippage'`, `'risk_stop_loss_rate'`, `source`, `target`.
- 위젯/컨테이너 key 표현식: `'risk_manager_enabled'`, `'rm_allowance'`, `'rm_capital'`, `'rm_fee'`, `'rm_import_backtest'`, `'rm_leverage'`, `'rm_slippage'`, `'rm_stop'`.

## ui/storage_view.py

[소스](../../ui/storage_view.py)

함수: `render_account_history`, `render_analysis_history`.

- Session State 참조: `'_macro_results'`, `'_portfolio_account'`, `'_portfolio_history'`, `'_portfolio_nonce'`, `'_portfolio_revision'`, `'_restored_analysis'`, `'_restored_analysis_generation'`, `'_workflow_execution'`.
- 위젯/컨테이너 key 표현식: `'pf_export'`, `'pf_import'`, `'pf_import_file'`, `'pf_restore'`, `'pf_save_valuation'`, `'pf_saved_account'`, `'restore_analysis_snapshot'`, `'save_analysis_snapshot'`, `'saved_analysis_id'`, `'snapshot_export'`.

## ui/strategy_builder.py

[소스](../../ui/strategy_builder.py)

함수: `_initialize`, `load_builder_json`, `_load_uploaded`, `_add_condition`, `_delete_condition`, `_choice`, `_render_direction`, `render_strategy_builder`.

- Session State 참조: `'_strategy_draft'`, `'_strategy_notice'`, `'_strategy_revision'`, `'strategy_name'`, `'strategy_upload'`, `key`, `number_key`.
- 위젯/컨테이너 key 표현식: `'strategy_download'`, `'strategy_load'`, `'strategy_name'`, `'strategy_upload'`, `f'strategy_add_{direction}'`, `f'{prefix}_delete'`, `f'{prefix}_kind'`, `f'{prefix}_left'`, `f'{prefix}_operator'`, `f'{prefix}_right'`, `logic_key`, `number_key`.

## ui/strategy_signals.py

[소스](../../ui/strategy_signals.py)

함수: `render_signal_preview`.

- Session State 참조: 없음.
- 위젯/컨테이너 key 표현식: 없음.

## ui/styles.py

[소스](../../ui/styles.py)

함수: `apply_custom_css`.

- Session State 참조: 없음.
- 위젯/컨테이너 key 표현식: 없음.

## ui/workflow_view.py

[소스](../../ui/workflow_view.py)

함수: `render_workflow_controls`, `input_signature`, `_show_report`, `render_workflow_result`.

- Session State 참조: `'_ai_ledger'`, `'_debate_last_attempt'`, `'_macro_results'`, `'_workflow_approval_signature'`, `'_workflow_execution'`, `'_workflow_signature'`, `'ai_cost_limit'`, `'ai_output_limit'`, `'ai_request_limit'`, `'period'`, `'symbol'`, `'timeframe'`, `'workflow_cost_approved'`.
- 위젯/컨테이너 key 표현식: `'debate_enabled'`, `'debate_rounds'`, `'run_multi_agent'`, `'workflow_cost_approved'`, `'workflow_mode'`.

