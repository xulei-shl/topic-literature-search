"""高级检索进度文件公共基类。"""

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from src.utils.result_output import build_output_slug


class BaseSearchProgressStore:
    """负责读写高级检索断点续跑进度文件。"""

    VERSION = 1
    SEARCH_PARAM_KEYS: tuple[str, ...] = ()
    BOOLEAN_PARAM_DEFAULTS: dict[str, bool] = {}
    FALLBACK_SLUG = "search"
    VALIDATION_ERROR_CLASS: type[Exception] = ValueError

    def __init__(self, file_path: Path) -> None:
        """初始化进度文件存储。

        Args:
            file_path: 进度文件路径。
        """
        self.file_path = Path(file_path).resolve()

    @classmethod
    def build_default_path(cls, output_dir: Path, **search_params: Any) -> Path:
        """根据检索参数生成稳定的默认进度文件路径。

        Args:
            output_dir: 输出目录。
            **search_params: 参与指纹计算的检索参数。

        Returns:
            Path: 默认进度文件路径。
        """
        fingerprint = cls._build_fingerprint(search_params)
        slug = build_output_slug(search_params.get("query", ""), cls.FALLBACK_SLUG)[:30]
        return Path(output_dir).resolve() / f"progress-{slug}-{fingerprint}.json"

    def exists(self) -> bool:
        """返回进度文件是否存在。

        Returns:
            bool: 文件是否存在。
        """
        return self.file_path.exists()

    @classmethod
    def prepare_store(
        cls,
        progress_file: Optional[Path],
        output_dir: Optional[Path] = None,
        **search_params: Any,
    ) -> tuple["BaseSearchProgressStore", Optional[dict[str, Any]]]:
        """根据显式或默认路径初始化进度文件存储。

        Args:
            progress_file: 显式传入的进度文件路径。
            output_dir: 默认进度文件所在目录。
            **search_params: 用于构造默认进度文件名的检索参数。

        Returns:
            tuple[BaseSearchProgressStore, Optional[dict[str, Any]]]:
                进度文件存储对象与已加载的历史进度。

        Raises:
            Exception: 无法确定进度文件路径时抛出配置的校验异常。
        """
        if progress_file is not None:
            store = cls(progress_file)
        else:
            if output_dir is None:
                raise cls._validation_error("高级检索至少需要提供 --query 或 --progress-file")
            default_path = cls.build_default_path(output_dir=output_dir, **search_params)
            store = cls(default_path)

        resume_data = store.load() if store.exists() else None
        return store, resume_data

    def load(self) -> dict[str, Any]:
        """读取进度文件。

        Returns:
            dict[str, Any]: 进度数据。

        Raises:
            Exception: 文件不存在、JSON 非法或格式错误时抛出配置的校验异常。
        """
        if not self.file_path.exists():
            raise self._validation_error(f"进度文件不存在: {self.file_path}")

        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise self._validation_error(f"进度文件不是合法 JSON: {self.file_path}") from exc

        if not isinstance(data, dict):
            raise self._validation_error(f"进度文件内容格式无效: {self.file_path}")
        return data

    def save(self, state: dict[str, Any]) -> str:
        """保存进度文件。

        Args:
            state: 进度状态。

        Returns:
            str: 进度文件路径字符串。
        """
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with self.file_path.open("w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)
        return str(self.file_path)

    @classmethod
    def resolve_search_params(
        cls,
        cli_params: dict[str, Any],
        progress_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """合并 CLI 参数与进度文件参数。

        Args:
            cli_params: CLI 参数。
            progress_data: 进度文件数据。

        Returns:
            dict[str, Any]: 合并后的检索参数。
        """
        progress_params = (progress_data or {}).get("search_params") or {}
        resolved: dict[str, Any] = {}

        for key in cls.SEARCH_PARAM_KEYS:
            cli_value = cli_params.get(key)
            progress_value = progress_params.get(key)

            if key in cls.BOOLEAN_PARAM_DEFAULTS:
                resolved[key] = cls._resolve_boolean_param(
                    key=key,
                    cli_value=cli_value,
                    progress_value=progress_value,
                )
                continue

            if cli_value is None:
                resolved[key] = progress_value
                continue

            if progress_value is not None and cli_value != progress_value:
                raise cls._validation_error(f"CLI 参数与进度文件不一致: {key}")
            resolved[key] = cli_value

        if not resolved.get("query"):
            raise cls._validation_error("高级检索至少需要提供 --query 或 --progress-file")
        return resolved

    @classmethod
    def _resolve_boolean_param(
        cls,
        key: str,
        cli_value: Any,
        progress_value: Any,
    ) -> bool:
        """处理布尔参数的默认值与冲突校验。

        Args:
            key: 参数名。
            cli_value: CLI 传入值。
            progress_value: 进度文件中的历史值。

        Returns:
            bool: 解析后的布尔值。
        """
        default_value = cls.BOOLEAN_PARAM_DEFAULTS[key]
        normalized_cli = default_value if cli_value is None else bool(cli_value)
        if progress_value is None:
            return normalized_cli

        normalized_progress = bool(progress_value)
        if normalized_cli != default_value and normalized_cli != normalized_progress:
            raise cls._validation_error(f"CLI 参数与进度文件不一致: {key}")
        return normalized_progress

    @classmethod
    def _build_fingerprint(cls, search_params: dict[str, Any]) -> str:
        """构造进度文件名使用的短指纹。

        Args:
            search_params: 检索参数字典。

        Returns:
            str: 指纹字符串。
        """
        payload = json.dumps(
            {key: search_params.get(key) for key in cls.SEARCH_PARAM_KEYS},
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]

    @classmethod
    def _validation_error(cls, message: str) -> Exception:
        """构造校验异常。

        Args:
            message: 异常信息。

        Returns:
            Exception: 配置的校验异常对象。
        """
        return cls.VALIDATION_ERROR_CLASS(message)
