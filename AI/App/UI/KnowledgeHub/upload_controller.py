"""
==========================================================
QA AI Studio
Knowledge Hub

Upload Controller

Version : 1.0
==========================================================
"""

from Core.upload_pipeline import UploadPipeline


class UploadController:

    def __init__(self):

        self.pipeline = UploadPipeline()

    # --------------------------------------------------

    def upload(
        self,
        files,
        domain,
        module,
        knowledge_name,
        version
    ):

        results = []

        for file in files:

            result = self.pipeline.upload(

                source=file,

                domain=domain,

                module=module,

                knowledge_name=knowledge_name,

                version=version

            )

            results.append(result)

        return results
