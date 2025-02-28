import pandas as pd
from pprint import pprint

if __name__ == "__main__":
    df = pd.read_csv("./sample.csv")

    for code, comment in zip(list(df.code_before), list(df.comment)): 
        print("CODE: ")
        print(code)
        print("COMMENT: ")
        print(comment)
        print("-"*80)
