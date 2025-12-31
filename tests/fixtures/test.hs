module Main where

import Data.List
import Control.Monad

data User = User 
  { userName :: String
  , userAge :: Int
  } deriving (Show, Eq)

greet :: User -> String
greet user = "Hello, " ++ userName user

processData :: [Int] -> [Int]
processData xs = filter (> 0) xs

main :: IO ()
main = do
  let user = User "Alice" 30
  putStrLn $ greet user
