from Core.knowledge_service import KnowledgeService


def main():

    service = KnowledgeService()


    result = service.upload(

        domain="PSW Domain",

        module="SD Warehousing",

        knowledge_name="SD Warehousing SRS",

        version="1.0",

        files=[
            r"D:\QA AI Studio-Repository"
        ]

    )


    print("\n==============================")
    print("UPLOAD RESULT")
    print("==============================")

    print(result)



if __name__ == "__main__":
    main()