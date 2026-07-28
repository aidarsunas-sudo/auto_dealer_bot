# Гейт rejected_posts.log — точный текст шага

Источник: шаг **«Проверить rejected_posts.log (гейт validate_post_before_publish)»**
в `.github/workflows/main_pipeline.yml` (ищите по названию шага, не по номеру
строки — идентичный шаг в `.github/workflows/run_bot.yml`, только с условием
`if: ${{ always() && github.event.inputs.test_post != 'true' }}` — пропускается
целиком в режиме `test_post`, там отказ гейта одному из перебираемых
кандидатов ожидаем, см. `test_post.py`).

```yaml
      - name: Проверить rejected_posts.log (гейт validate_post_before_publish)
        if: always()
        run: |
          if [ -s rejected_posts.log ]; then
            echo "::warning::validate_post_before_publish() отклонил пост(ы) в этом прогоне (легитимный отказ, не поломка):"
            cat rejected_posts.log
            {
              echo "### ⚠️ Гейт validate_post_before_publish() отклонил лот(ы)"
              echo '```'
              cat rejected_posts.log
              echo '```'
            } >> "$GITHUB_STEP_SUMMARY"
          else
            echo "rejected_posts.log пуст — гейт ничего не отклонил в этом прогоне"
          fi
```

## Кто пишет в rejected_posts.log

Переменная `REJECTED_LOG_PATH` в начале `post_validator.py` —
`REJECTED_LOG_PATH = Path(__file__).resolve().parent / "rejected_posts.log"`.

Единственная функция записи — `_log_rejection()`, вызывается из
`validate_post_before_publish()` и `check_price_vs_batch_median()` — оба места
чисто детерминированные проверки (фото/название/год/пробег/цена), без единого
`except`-блока рядом.

## Где независимый сигнал реальной ошибки

Конец `main_pipeline.py` (аналогично — конец `main.py`):

```python
def main() -> None:
    try:
        run()
    except Exception as error:
        error_details = traceback.format_exc()
        logging.getLogger("main_pipeline").critical(f"КРИТИЧЕСКАЯ ОШИБКА:\n{error_details}")
        notify_error(f"Критическая ошибка, main_pipeline остановился:\n{error}\n\n"
                      f"Подробности — в logs/main_pipeline.log")
        raise
```

`raise` в конце — процесс завершается ненулевым кодом, шаг «Запустить
main_pipeline» (который идёт ДО гейта rejected_posts.log) сам красит job в
failure, независимо от того, что делает следующий шаг.
