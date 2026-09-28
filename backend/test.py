from langchain_cloudflare.chat_models import ChatCloudflareWorkersAI

from dotenv import load_dotenv
load_dotenv()

llm = ChatCloudflareWorkersAI(
    model="@cf/meta/llama-3.3-70b-instruct-fp8-fast",  # check current models with `npx wrangler ai models list`
)

response = llm.invoke("Explain neural networks in 2 lines")
print(response.content)