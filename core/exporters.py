import shutil
import subprocess
import zipfile

from .state import job_dir


def write_pdf_from_markdown(markdown_path, pdf_path):
    pandoc = shutil.which("pandoc")
    xelatex = shutil.which("xelatex")
    if not pandoc or not xelatex:
        return False, "未找到 pandoc 或 xelatex，跳过 PDF 生成。"

    link_style_path = markdown_path.parent / "pdf_link_style.tex"
    link_style_path.write_text(
        "\\usepackage[normalem]{ulem}\n"
        "\\let\\DeepResearchOldHref\\href\n"
        "\\renewcommand{\\href}[2]{\\DeepResearchOldHref{#1}{\\textcolor{blue}{\\uline{#2}}}}\n",
        encoding="utf-8",
    )

    command = [
        pandoc,
        str(markdown_path.name),
        "-o",
        str(pdf_path.name),
        "--pdf-engine=xelatex",
        "-V",
        "CJKmainfont=Songti SC",
        "-V",
        "geometry:margin=1in",
        "-V",
        "colorlinks=true",
        "-V",
        "linkcolor=blue",
        "-V",
        "urlcolor=blue",
        "-V",
        "citecolor=blue",
        f"--include-in-header={link_style_path.name}",
        "--resource-path=.:images",
    ]
    try:
        subprocess.run(
            command,
            cwd=markdown_path.parent,
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        return False, exc.stderr or str(exc)
    return True, ""


def make_artifact_zip(job_id):
    base = job_dir(job_id)
    archive = base / "research_artifacts.zip"
    include_names = [
        "state.json",
        "research_progress.md",
        "research_plan.md",
        "research_report.md",
        "research_report.original.md",
        "research_report.pdf",
        "citation_metadata.json",
        "interaction_plan.json",
        "interaction_final.json",
        "chat_completion.json",
    ]
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in include_names:
            path = base / name
            if path.exists() and path.is_file():
                zf.write(path, arcname=name)
        images_dir = base / "images"
        if images_dir.exists():
            for image in sorted(images_dir.iterdir()):
                if image.is_file():
                    zf.write(image, arcname=f"images/{image.name}")
    return archive
